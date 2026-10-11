"""A stand-in for Clara's world service that plays the scripted run from mock_script.

    .venv/bin/uvicorn observer.mock_world:app --port 8200

It has the same endpoints as the real world service, so the UI only changes a URL
to switch. Every event goes through observer.client.emit to the real observer,
exactly like the agents' events will.

Settings:
    OBSERVER_URL  where the observer is (default http://localhost:8100)
    MOCK_SPEED    how many times faster than real time to play (default 1)
    MOCK_SEED     random seed for the scripted run (default 7)

Endpoints:
    POST /runs {seed}          returns {run_id}, then the town comes online
    POST /runs/{run_id}/start  starts the run once run.ready has been sent
"""

import asyncio
import logging
import os
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from observer.client import aemit
from observer.mock_script import Step, prepare_timeline, run_timeline

log = logging.getLogger("mock_world")

app = FastAPI(title="Oddtown mock world service")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

runs: dict[str, dict] = {}
background: set[asyncio.Task] = set()


class NewRun(BaseModel):
    seed: str = Field(min_length=1, max_length=500)


def spawn(coro) -> None:
    task = asyncio.create_task(coro)
    background.add(task)
    task.add_done_callback(background.discard)


async def play(run_id: str, steps: list[Step]) -> None:
    speed = float(os.environ.get("MOCK_SPEED", "1"))
    url = os.environ.get("OBSERVER_URL")
    loop = asyncio.get_running_loop()
    start = loop.time()
    for t, type, agent_id, payload in steps:
        delay = start + t / speed - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)
        if not await aemit(run_id, type, payload, agent_id, url=url):
            log.warning("observer did not take %s for %s", type, run_id)


@app.post("/runs")
async def create_run(body: NewRun):
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    run = {"seed": body.seed.strip(), "state": "preparing"}
    runs[run_id] = run

    async def prepare():
        await play(run_id, prepare_timeline(run["seed"]))
        run["state"] = "ready"

    spawn(prepare())
    return {"run_id": run_id}


@app.post("/runs/{run_id}/start")
async def start_run(run_id: str):
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(404, "unknown run")
    if run["state"] != "ready":
        raise HTTPException(409, f"run is {run['state']}, not ready")
    run["state"] = "running"

    async def go():
        await play(run_id, run_timeline(run["seed"], int(os.environ.get("MOCK_SEED", "7"))))
        run["state"] = "ended"

    spawn(go())
    return {"run_id": run_id}
