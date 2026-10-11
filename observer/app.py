"""The observer: stores every event and streams each run to the UI.

    .venv/bin/uvicorn observer.app:app --port 8100

Set OBSERVER_DB to use a database file other than observer.db. Run it with one
worker, since it must be the only process writing to the database.

Endpoints:
    POST /events                         store one event, returns {"seq": n}
    GET  /runs                           every run, newest first
    GET  /runs/{run_id}/events?after=n   a run's events with seq > n
    WS   /ws/{run_id}?after=n            the run's past events, then new ones live
"""

import asyncio
import logging
import os
from collections import defaultdict
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from observer.events import parse_event
from observer.store import Store

log = logging.getLogger("observer")


def create_app(db_path: str) -> FastAPI:
    app = FastAPI(title="Oddtown observer")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    store = Store(db_path)
    subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

    @app.post("/events")
    async def post_event(data: dict[str, Any]):
        try:
            event = parse_event(data)
        except ValidationError as e:
            log.warning("rejected %s event: %s", data.get("type"), e)
            raise HTTPException(422, detail=e.errors(include_url=False, include_context=False))
        stored = store.append(event)
        for queue in subscribers.get(stored["run_id"], ()):
            queue.put_nowait(stored)
        return {"seq": stored["seq"]}

    @app.get("/runs")
    async def get_runs():
        return store.runs()

    @app.get("/runs/{run_id}/events")
    async def get_events(run_id: str, after: int = 0):
        return store.events(run_id, after)

    @app.websocket("/ws/{run_id}")
    async def stream(ws: WebSocket, run_id: str, after: int = 0):
        await ws.accept()
        # Subscribe before reading the backlog, so nothing stored in between is
        # missed. Anything that shows up in both gets skipped by seq.
        queue: asyncio.Queue = asyncio.Queue()
        subscribers[run_id].add(queue)
        try:
            last = after
            for event in store.events(run_id, after):
                await ws.send_json(event)
                last = event["seq"]

            async def send_live():
                nonlocal last
                while True:
                    event = await queue.get()
                    if event["seq"] > last:
                        await ws.send_json(event)
                        last = event["seq"]

            async def wait_for_close():
                while True:
                    await ws.receive_text()

            # Whichever ends first, a failed send or the client closing, ends the stream.
            tasks = [asyncio.create_task(send_live()), asyncio.create_task(wait_for_close())]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                task.exception()
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            subscribers[run_id].discard(queue)
            if not subscribers[run_id]:
                subscribers.pop(run_id, None)

    return app


app = create_app(os.environ.get("OBSERVER_DB", "observer.db"))
