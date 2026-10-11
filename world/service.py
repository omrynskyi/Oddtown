"""The world service. It starts runs, injects events and ends runs. It never decides.

    python -m world.service --port 8100

It joins the town as the agent "world" (kind "world"), so it hears every
broadcast like a character does and can answer spawn requests. Everything it
does is one of these:

    POST /run                     start a run: launch the cast, wait for them, broadcast the seed
    POST /run/inject              broadcast a constraint or feedback as a world.event
    POST /run/human/{id}/answer   broadcast a human's answer to a human.request
    POST /run/agents/{id}/kill    SIGKILL one character, for the crash demo
    POST /run/stop                end the run now
    GET  /run                     status, live support and, once over, the result

A run ends when a majority of living characters have broadcast `rest`, the
time budget runs out, the token budget runs out, or someone calls /run/stop.
Then it adds up living support into up to three finalists, emits run.ended,
broadcasts a stop event and shuts the town down.

One run at a time per world process. The town namespace (ODDTOWN_TOWN) is
fixed for the life of the process, and each run gets a fresh run id.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from observer.events import emit
from transport.adapter import DeadPeerError
from transport.town import Town
from world.launcher import ROOT, Launcher
from world.ledger import Ledger

log = logging.getLogger("world")

WORLD_ID = "world"
DEFAULT_CAST = ROOT / "personas" / "starting_cast.json"


class StartRun(BaseModel):
    seed: str
    cast: list[dict] | None = None  # defaults to personas/starting_cast.json
    time_budget_s: float = 15 * 60
    token_budget: int = 1_500_000
    ready_timeout_s: float = 60
    stop_grace_s: float = 5


class Inject(BaseModel):
    kind: str = Field("constraint", description="constraint or feedback")
    text: str


class HumanAnswer(BaseModel):
    answer: str


class Run:
    def __init__(self, request: StartRun, run_dir: Path):
        self.id = run_dir.name
        self.request = request
        self.run_dir = run_dir
        self.ledger = Ledger()
        self.state = "starting"  # starting, running, ending, ended
        self.started_at = time.monotonic()
        self.tokens = 0
        self.human_requests: dict[str, dict] = {}
        self.result: dict | None = None
        self.launcher: Launcher | None = None
        self.watchdog: asyncio.Task | None = None


class World:
    def __init__(self):
        self.town = Town.from_env(WORLD_ID)
        self.run: Run | None = None
        self._end_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        await self.town.join({"kind": "world", "name": "World"}, self.on_envelope)

    async def shutdown(self) -> None:
        if self.run and self.run.state in ("starting", "running"):
            await self.end_run("world shutting down")
        await self.town.leave()

    def emit(self, type: str, payload: dict, agent_id: str | None = WORLD_ID) -> None:
        emit(type, agent_id, payload, run_id=self.run.id if self.run else None)

    async def living(self) -> set[str]:
        return {r["id"] for r in await self.town.characters()}

    # -- runs -------------------------------------------------------------

    async def start_run(self, request: StartRun) -> dict:
        if self.run and self.run.state != "ended":
            raise HTTPException(409, "a run is already going, stop it first")
        cast = request.cast if request.cast is not None else json.loads(DEFAULT_CAST.read_text())
        if not cast:
            raise HTTPException(400, "the cast is empty")
        ids = [p.get("id") for p in cast]
        if len(set(ids)) != len(ids) or not all(ids):
            raise HTTPException(400, "every persona needs a unique id")

        run_id = f"run_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        run = Run(request, ROOT / ".oddtown" / "runs" / run_id)
        self.run = run
        await self._clear_stale_records()
        run.launcher = Launcher(
            run.run_dir,
            {"RUN_ID": run.id, "ODDTOWN_TOWN": os.environ.get("ODDTOWN_TOWN", "oddtown")},
            self.on_exit,
        )
        self.emit("run.started", {"seed": request.seed, "cast": ids})
        for persona in cast:
            await run.launcher.start(persona)

        # Wait for every character to register before seeding, so nobody misses it.
        deadline = time.monotonic() + request.ready_timeout_s
        while not set(ids) <= await self.living():
            if time.monotonic() > deadline:
                missing = sorted(set(ids) - await self.living())
                await self.end_run(f"characters never joined: {', '.join(missing)}")
                raise HTTPException(504, f"characters never joined: {missing}")
            await asyncio.sleep(0.2)

        run.state = "running"
        run.started_at = time.monotonic()
        run.watchdog = asyncio.create_task(self._watchdog(run))
        await self.world_event("seed", request.seed)
        return {"run_id": run.id, "agents": ids}

    async def _clear_stale_records(self) -> None:
        """Characters left behind by a crashed earlier run would get the seed too."""
        for record in await self.town.characters():
            await self.town.registry.remove(record["id"])

    async def world_event(self, kind: str, text: str, **extra) -> list[str]:
        body = {"kind": kind, "text": text, **extra}
        self.emit("world.event", body)
        dead = await self.town.broadcast({"type": "world.event", "body": body})
        for agent_id in dead:
            self.emit("agent.bounced", {"agent_id": agent_id, "noticed_by": WORLD_ID})
        return dead

    async def _watchdog(self, run: Run) -> None:
        while run.state == "running":
            await asyncio.sleep(0.5)
            if time.monotonic() - run.started_at > run.request.time_budget_s:
                await self.end_run("time budget")
            elif run.tokens > run.request.token_budget:
                await self.end_run("token budget")

    async def end_run(self, reason: str) -> dict:
        async with self._end_lock:
            run = self.run
            if run is None or run.state in ("ending", "ended"):
                return run.result if run else {}
            run.state = "ending"
            living = await self.living()
            tally = run.ledger.finalists(living)
            run.result = {"reason": reason, "living": sorted(living), **tally}
            self.emit("run.ended", run.result)
            for tied in tally["ties"]:
                await self._human_request("tie", {"branch_ids": tied}, requested_by=WORLD_ID)
            await self.world_event("stop", reason)
            if run.launcher:
                await run.launcher.stop_all(grace=run.request.stop_grace_s)
            await self._clear_stale_records()
            run.state = "ended"
            if run.watchdog and run.watchdog is not asyncio.current_task():
                run.watchdog.cancel()
            return run.result

    # -- what the town says to the world ----------------------------------

    async def on_envelope(self, envelope: dict) -> None:
        run = self.run
        if run is None or run.state not in ("starting", "running"):
            return
        run.ledger.observe(envelope)
        kind = envelope.get("type")
        sender = envelope.get("from")
        body = envelope.get("body") or {}
        if kind == "agent.last_words":
            self.emit("agent.died", {"agent_id": sender, "last_words": body.get("text")}, agent_id=sender)
        elif kind == "spawn.request":
            await self._spawn(sender, body)
        elif kind == "usage.report":
            run.tokens += int(body.get("tokens", 0))
        elif kind == "human.request":
            await self._human_request(body.get("kind", "other"), body, requested_by=sender)
        if run.state == "running" and kind in ("rest", "agent.last_words"):
            if run.ledger.majority_resting(await self.living()):
                task = asyncio.create_task(self.end_run("town at rest"))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def _spawn(self, sender: str, body: dict) -> None:
        persona = body.get("persona") or {}
        try:
            agent_id = await self.run.launcher.start(persona)
            self.emit("agent.spawned", {"agent_id": agent_id, "requested_by": sender, "persona": persona})
            reply = {"type": "spawn.result", "body": {"ok": True, "agent_id": agent_id}}
        except ValueError as exc:
            reply = {"type": "spawn.result", "body": {"ok": False, "error": str(exc)}}
        try:
            await self.town.send(sender, reply)
        except DeadPeerError:
            pass  # the designer died after asking, the newcomer still gets born

    async def _human_request(self, kind: str, details: dict, requested_by: str) -> str:
        request_id = f"hr_{uuid.uuid4().hex[:8]}"
        request = {"request_id": request_id, "kind": kind, "requested_by": requested_by, "details": details}
        self.run.human_requests[request_id] = {**request, "answer": None}
        self.emit("human.request", request)
        return request_id

    async def answer_human(self, request_id: str, answer: str) -> None:
        run = self.run
        if run is None or request_id not in run.human_requests:
            raise HTTPException(404, "no such human request")
        run.human_requests[request_id]["answer"] = answer
        self.emit("human.answer", {"request_id": request_id, "answer": answer})
        if run.state == "running":
            await self.world_event("human.answer", answer, request_id=request_id)

    async def on_exit(self, agent_id: str, code: int) -> None:
        run = self.run
        if run is None:
            return
        if code != 0 and run.state in ("starting", "running"):
            # The world only reports it. Peers find out on their own when a send bounces.
            self.emit("agent.crashed", {"agent_id": agent_id, "exit_code": code}, agent_id=agent_id)
        else:
            self.emit("agent.exited", {"agent_id": agent_id, "exit_code": code}, agent_id=agent_id)

    def status(self) -> dict:
        run = self.run
        if run is None:
            return {"state": "idle"}
        return {
            "run_id": run.id,
            "state": run.state,
            "seed": run.request.seed,
            "elapsed_s": round(time.monotonic() - run.started_at, 1),
            "tokens": run.tokens,
            "processes": sorted(run.launcher.running()) if run.launcher else [],
            "endorsements": run.ledger.endorsements,
            "resting": sorted(run.ledger.resting),
            "dead": sorted(run.ledger.dead),
            "human_requests": list(run.human_requests.values()),
            "result": run.result,
        }


def create_app() -> FastAPI:
    world = World()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await world.start()
        yield
        await world.shutdown()

    app = FastAPI(title="Oddtown world service", lifespan=lifespan)
    app.state.world = world

    @app.post("/run")
    async def start_run(request: StartRun):
        return await world.start_run(request)

    @app.get("/run")
    async def status():
        return {**world.status(), "living": sorted(await world.living())}

    @app.post("/run/inject")
    async def inject(event: Inject):
        if not world.run or world.run.state != "running":
            raise HTTPException(409, "no run is going")
        dead = await world.world_event(event.kind, event.text)
        return {"ok": True, "bounced": dead}

    @app.post("/run/human/{request_id}/answer")
    async def answer(request_id: str, body: HumanAnswer):
        await world.answer_human(request_id, body.answer)
        return {"ok": True}

    @app.post("/run/agents/{agent_id}/kill")
    async def kill(agent_id: str):
        if not world.run or not world.run.launcher or not world.run.launcher.kill(agent_id):
            raise HTTPException(404, "no such running character")
        return {"ok": True}

    @app.post("/run/stop")
    async def stop():
        if not world.run:
            raise HTTPException(409, "no run to stop")
        return await world.end_run("stopped by hand")

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Oddtown world service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("WORLD_PORT", "8100")))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    import uvicorn

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
