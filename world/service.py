"""The world service. It starts runs, injects events and ends runs. It never decides.

    python -m world.service --port 8200

It joins the town as the agent "world" (kind "world"), so it hears every
broadcast like a character does and can answer spawn requests. Everything it
does is one of these:

    POST /runs                              launch the cast; emits run.created, agent.born each, run.ready
    POST /runs/{run_id}/start               broadcast the seed; emits run.started
    POST /runs/{run_id}/inject              broadcast a constraint or feedback as a world.event
    POST /runs/{run_id}/human/{id}/answer   broadcast a human's answer to a human.request
    POST /runs/{run_id}/agents/{id}/kill    SIGKILL one character, for the crash demo
    POST /runs/{run_id}/stop                end the run now
    GET  /runs/{run_id}                     status, live support and, once over, the result

A run ends when a majority of living characters have broadcast `rest`, the
time budget runs out, the token budget runs out, or someone calls stop. Then
it adds up living support into up to three finalists, emits run.ended,
broadcasts a stop event and shuts the town down.

Events go to the observer through observer.client, so they follow Oleg's
contract in observer/events.py.

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
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agents import config as agent_config
from observer.client import aemit
from transport.adapter import DeadPeerError
from transport.town import Town
from world.launcher import ROOT, Launcher
from world.ledger import Ledger

log = logging.getLogger("world")

WORLD_ID = "world"
DEFAULT_CAST = ROOT / "personas" / "starting_cast.json"
SEED_BRANCH_ID = "br_seed"


class NewRun(BaseModel):
    seed: str = Field(min_length=1, max_length=500)
    cast: list[dict] | None = None  # defaults to personas/starting_cast.json
    time_budget_s: float = 15 * 60
    token_budget: int = 1_500_000
    ready_timeout_s: float = 60
    stop_grace_s: float = 5


class Inject(BaseModel):
    kind: str = Field("constraint", pattern="^(constraint|feedback)$")
    text: str = Field(min_length=1)


class HumanAnswer(BaseModel):
    answer: str


class Run:
    def __init__(self, request: NewRun, run_dir: Path):
        self.id = run_dir.name
        self.request = request
        self.run_dir = run_dir
        self.ledger = Ledger()
        # preparing -> ready -> running -> ending -> ended, or failed if the cast never joins
        self.state = "preparing"
        self.started_at = time.monotonic()
        self.tokens = 0
        self.human_requests: dict[str, dict] = {}
        self.result: dict | None = None
        self.launcher: Launcher | None = None
        self.cast_ids: list[str] = []
        self.born: set[str] = set()


class World:
    def __init__(self):
        self.town = Town.from_env(WORLD_ID)
        self.run: Run | None = None
        self._end_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        await self.town.join({"kind": "world", "name": "World"}, self.on_envelope)

    async def shutdown(self) -> None:
        if self.run and self.run.state in ("preparing", "ready", "running"):
            await self.end_run("world shutting down")
        await self.town.leave()

    async def emit(self, type: str, payload: dict, agent_id: str | None = None) -> None:
        if self.run:
            await aemit(self.run.id, type, payload, agent_id)

    async def living(self) -> set[str]:
        return {r["id"] for r in await self.town.characters()}

    def current(self, run_id: str) -> Run:
        if self.run is None or self.run.id != run_id:
            raise HTTPException(404, "unknown run")
        return self.run

    # -- runs -------------------------------------------------------------

    async def create_run(self, request: NewRun) -> dict:
        if self.run and self.run.state not in ("ended", "failed"):
            raise HTTPException(409, "a run is already going, stop it first")
        cast = request.cast if request.cast is not None else json.loads(DEFAULT_CAST.read_text())
        if not cast:
            raise HTTPException(400, "the cast is empty")
        ids = [p.get("id") for p in cast]
        if len(set(ids)) != len(ids) or not all(ids):
            raise HTTPException(400, "every persona needs a unique id")

        run_id = f"run_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        run = Run(request, ROOT / ".oddtown" / "runs" / run_id)
        run.cast_ids = ids
        self.run = run
        await self._clear_stale_records()
        run.launcher = Launcher(
            run.run_dir,
            {"RUN_ID": run.id, "ODDTOWN_TOWN": os.environ.get("ODDTOWN_TOWN", "oddtown")},
            self.on_exit,
        )
        await self.emit("run.created", {"seed": request.seed})
        self.spawn(self._bring_online(run, cast))
        return {"run_id": run.id}

    async def _bring_online(self, run: Run, cast: list[dict]) -> None:
        """Launch the cast, emit agent.born as each one joins, then run.ready."""
        try:
            for persona in cast:
                await run.launcher.start(persona)
            personas = {p["id"]: p for p in cast}
            deadline = time.monotonic() + run.request.ready_timeout_s
            while run.state == "preparing":
                living = await self.living()
                for agent_id in run.cast_ids:
                    if agent_id in living and agent_id not in run.born:
                        run.born.add(agent_id)
                        await self.emit("agent.born", {"persona": personas[agent_id]}, agent_id)
                if run.born >= set(run.cast_ids):
                    run.state = "ready"
                    await self.emit("run.ready", {"agent_ids": run.cast_ids})
                    return
                if time.monotonic() > deadline:
                    missing = sorted(set(run.cast_ids) - run.born)
                    await self.end_run(f"characters never joined: {', '.join(missing)}", failed=True)
                    return
                await asyncio.sleep(0.2)
        except Exception as exc:
            log.exception("bringing the town online failed")
            await self.end_run(f"launch failed: {exc}", failed=True)

    async def start_run(self, run_id: str) -> dict:
        run = self.current(run_id)
        if run.state != "ready":
            raise HTTPException(409, f"run is {run.state}, not ready")
        run.state = "running"
        run.started_at = time.monotonic()
        self.spawn(self._watchdog(run))
        # The characters' quorum, so the UI badges the same proposals they see.
        await self.emit("run.started", {"quorum": agent_config.QUORUM if agent_config.on("quorum") else None})
        seed_branch = {
            "branch_id": SEED_BRANCH_ID,
            "parent_ids": [],
            "op": "seed",
            "authors": [WORLD_ID],
            "text": run.request.seed,
        }
        await self.world_event("seed", run.request.seed, branch=seed_branch)
        await self.emit("branch.created", seed_branch)
        return {"run_id": run.id}

    async def _clear_stale_records(self) -> None:
        """Characters left behind by a crashed earlier run would get the seed too."""
        for record in await self.town.characters():
            await self.town.registry.remove(record["id"])

    async def world_event(self, kind: str, text: str, **extra) -> list[str]:
        body = {"kind": kind, "text": text, **extra}
        # The observer's world.event only covers seed, constraint and feedback.
        # Stop and human answers show up there as run.ended and human.answer.
        if kind in ("seed", "constraint", "feedback"):
            await self.emit("world.event", {"kind": kind, "text": text})
        dead = await self.town.broadcast({"type": "world.event", "body": body})
        for agent_id in dead:
            await self.emit("agent.bounced", {"noticed_by": WORLD_ID}, agent_id)
        return dead

    async def _watchdog(self, run: Run) -> None:
        while run.state == "running":
            await asyncio.sleep(0.5)
            if time.monotonic() - run.started_at > run.request.time_budget_s:
                await self.end_run("time budget")
            elif run.tokens > run.request.token_budget:
                await self.end_run("token budget")

    async def end_run(self, reason: str, failed: bool = False) -> dict:
        async with self._end_lock:
            run = self.run
            if run is None or run.state in ("ending", "ended", "failed"):
                return run.result if run else {}
            run.state = "ending"
            living = await self.living()
            tally = run.ledger.finalists(living)
            run.result = {"reason": reason, "living": sorted(living), **tally}
            if not failed:
                await self.emit("run.ended", self._run_ended_payload(run.result))
                for tied in tally["ties"]:
                    await self._human_request("tie", {"branch_ids": tied}, requested_by=WORLD_ID)
            await self.world_event("stop", reason)
            if run.launcher:
                await run.launcher.stop_all(grace=run.request.stop_grace_s)
            await self._clear_stale_records()
            run.state = "failed" if failed else "ended"
            return run.result

    @staticmethod
    def _run_ended_payload(result: dict) -> dict:
        """Fit the tally to the observer's run.ended. Extra fields ride along."""
        finalists = []
        for f in result["finalists"]:
            report = f["minority_report"]
            finalists.append(
                {
                    "branch_id": f["branch_id"],
                    "text": (f["branch"] or {}).get("text", ""),
                    "champions": [c["agent_id"] for c in f["champions"]],
                    "minority_report": report["reason"] if report else "",
                    "minority_report_by": report["agent_id"] if report else None,
                    "support": f["support"],
                    "reasons": {c["agent_id"]: c["reason"] for c in f["champions"] if c["reason"]},
                }
            )
        # The baseline (one LLM's three ideas) comes from Anthony's agents/baseline.py once it exists.
        return {
            "finalists": finalists,
            "baseline": [],
            "reason": result["reason"],
            "ties": result["ties"],
            "living": result["living"],
        }

    # -- what the town says to the world ----------------------------------

    async def on_envelope(self, envelope: dict) -> None:
        run = self.run
        if run is None or run.state not in ("preparing", "ready", "running"):
            return
        run.ledger.observe(envelope)
        kind = envelope.get("type")
        sender = envelope.get("from")
        body = envelope.get("body") or {}
        if kind == "spawn.request":
            await self._spawn(sender, body)
        elif kind == "usage.report":
            run.tokens += int(body.get("tokens", 0))
        elif kind == "human.request":
            await self._human_request(body.get("kind", "other"), body, requested_by=sender)
        if run.state == "running" and kind in ("rest", "agent.last_words"):
            if run.ledger.majority_resting(await self.living()):
                self.spawn(self.end_run("town at rest"))

    async def _spawn(self, sender: str, body: dict) -> None:
        persona = body.get("persona") or {}
        try:
            agent_id = await self.run.launcher.start(persona)
            self.spawn(self._announce_birth(self.run, persona, sender))
            reply = {"type": "spawn.result", "body": {"ok": True, "agent_id": agent_id}}
        except ValueError as exc:
            reply = {"type": "spawn.result", "body": {"ok": False, "error": str(exc)}}
        try:
            await self.town.send(sender, reply)
        except DeadPeerError:
            pass  # the designer died after asking, the newcomer still gets born

    async def _announce_birth(self, run: Run, persona: dict, requested_by: str) -> None:
        """Emit agent.born once the newcomer has actually joined the town."""
        deadline = time.monotonic() + run.request.ready_timeout_s
        while run.state in ("ready", "running") and time.monotonic() < deadline:
            if persona["id"] in await self.living():
                run.born.add(persona["id"])
                await self.emit("agent.born", {"persona": persona, "requested_by": requested_by}, persona["id"])
                return
            await asyncio.sleep(0.2)

    async def _human_request(self, kind: str, details: dict, requested_by: str) -> str:
        request_id = f"hr_{uuid.uuid4().hex[:8]}"
        request = {"request_id": request_id, "kind": kind, "requested_by": requested_by, "details": details}
        self.run.human_requests[request_id] = {**request, "answer": None}
        await self.emit("human.request", request, requested_by)
        return request_id

    async def answer_human(self, run_id: str, request_id: str, answer: str) -> None:
        run = self.current(run_id)
        if request_id not in run.human_requests:
            raise HTTPException(404, "no such human request")
        run.human_requests[request_id]["answer"] = answer
        await self.emit("human.answer", {"request_id": request_id, "answer": answer})
        if run.state == "running":
            await self.world_event("human.answer", answer, request_id=request_id)

    async def on_exit(self, agent_id: str, code: int) -> None:
        run = self.run
        if run is None:
            return
        if code != 0 and run.state in ("preparing", "ready", "running"):
            # The world only reports it. Peers find out on their own when a send bounces.
            await self.emit("agent.crashed", {"exit_code": code}, agent_id)
        else:
            await self.emit("agent.exited", {"exit_code": code}, agent_id)

    async def status(self, run_id: str) -> dict:
        run = self.current(run_id)
        return {
            "run_id": run.id,
            "state": run.state,
            "seed": run.request.seed,
            "elapsed_s": round(time.monotonic() - run.started_at, 1),
            "tokens": run.tokens,
            "processes": sorted(run.launcher.running()) if run.launcher else [],
            "living": sorted(await self.living()),
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
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.state.world = world

    @app.post("/runs")
    async def create_run(request: NewRun):
        return await world.create_run(request)

    @app.post("/runs/{run_id}/start")
    async def start_run(run_id: str):
        return await world.start_run(run_id)

    @app.get("/runs/{run_id}")
    async def status(run_id: str):
        return await world.status(run_id)

    @app.post("/runs/{run_id}/inject")
    async def inject(run_id: str, event: Inject):
        if world.current(run_id).state != "running":
            raise HTTPException(409, "the run isn't going")
        dead = await world.world_event(event.kind, event.text)
        return {"ok": True, "bounced": dead}

    @app.post("/runs/{run_id}/human/{request_id}/answer")
    async def answer(run_id: str, request_id: str, body: HumanAnswer):
        await world.answer_human(run_id, request_id, body.answer)
        return {"ok": True}

    @app.post("/runs/{run_id}/agents/{agent_id}/kill")
    async def kill(run_id: str, agent_id: str):
        run = world.current(run_id)
        if not run.launcher or not run.launcher.kill(agent_id):
            raise HTTPException(404, "no such running character")
        return {"ok": True}

    @app.post("/runs/{run_id}/stop")
    async def stop(run_id: str):
        world.current(run_id)
        return await world.end_run("stopped by hand")

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Oddtown world service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("WORLD_PORT", "8200")))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    import uvicorn

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
