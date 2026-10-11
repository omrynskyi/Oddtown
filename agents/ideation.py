"""A character in the ideation voting loop (world/ideation.py), not the town.

character.py runs this instead of the town's Character when the world starts
the process with ODDTOWN_MODE=ideation_loop. Nobody dies or gets replaced in
this mode, so the agent count stays fixed for the counts to work.

It does three things, each one LLM call through the persona's own model:

    ideation.originate  write one idea from the seed, broadcast it
    ideation.twist      twist every idea handed over that isn't linked to me
                        (round 1: everyone else's original; later: all N), one call for all
    ideation.vote       vote for exactly one idea I'm not linked to

Ideas and votes are broadcast, so every agent and the world hear them.
Observer events: branch.created for each idea (parent = the idea it twisted,
author = the last editor), loop.vote for each vote, agent.decision for the
speech bubble, llm.call for each call.
"""

from __future__ import annotations

import asyncio
import logging
import random
import secrets

from pydantic import BaseModel, Field

from agents import llm
from agents.brain import clip, persona_text
from agents.llm import LLMError, Section
from observer.client import aemit
from transport.town import Town

log = logging.getLogger(__name__)

IDEA_MAX = 280
SEED_IDEA_ID = "br_seed"

RULES = """You are one of several characters in an ideation loop, each with a strong worldview of your own. Over a few rounds, everyone writes ideas from one seed, twists each other's ideas, and votes. Each round, only the most-voted ideas survive and get twisted again.

When you write or twist an idea, reshape it through your own worldview, loves, hates and red line, so it becomes clearly yours, but keep it a concrete idea someone could actually build or do. One or two sentences, under 280 characters, no preamble.

When you vote, you see only ideas you didn't last edit. Vote for the single idea you find most original that still makes sense, judged through your own worldview."""


class NewIdea(BaseModel):
    text: str = Field(description="The idea, one or two sentences, under 280 characters")


class Twist(BaseModel):
    idea_id: str = Field(description="The id of the idea you twisted")
    text: str = Field(description="Your twisted version, one or two sentences, under 280 characters")


class Twists(BaseModel):
    twists: list[Twist]


class Vote(BaseModel):
    idea_id: str = Field(description="The id of the one idea you vote for")
    reason: str = Field(description="Why, in one short sentence")


def new_idea_id() -> str:
    return f"br_{secrets.token_hex(3)}"


def ideas_section(ideas: list[dict]) -> Section:
    return Section("ideas", "\n".join(f"[{i['idea_id']}] {i['text']}" for i in ideas))


class IdeationAgent:
    def __init__(self, persona: dict, run_id: str, town: Town):
        self.persona = persona
        self.id = persona["id"]
        self.run_id = run_id
        self.town = town
        self.rng = random.Random(secrets.randbits(32))
        self.done = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        self._events: asyncio.Queue = asyncio.Queue()
        self.seed = ""
        self.constraints: list[str] = []

    # -- plumbing -------------------------------------------------------------

    def spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(self._report_crash)

    def _report_crash(self, task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception():
            log.error("ideation step failed", exc_info=task.exception())
            self.emit("agent.error", {"error": repr(task.exception())[:500]})

    def emit(self, type: str, payload: dict) -> None:
        self._events.put_nowait((type, payload))

    async def _emitter(self) -> None:
        """One queue, so this agent's events reach the observer in order."""
        while True:
            type, payload = await self._events.get()
            try:
                await aemit(self.run_id, type, payload, self.id)
            finally:
                self._events.task_done()

    async def world_id(self) -> str | None:
        worlds = await self.town.registry.search(kind="world")
        return worlds[0]["id"] if worlds else None

    async def think(self, kind: str, user: list[Section], out, mock):
        system = [Section("rules", RULES), Section("persona", persona_text(self.persona))]
        result = await llm.call_json(
            kind=kind, model=self.persona.get("model"), system=system, user=user, out=out, mock=mock,
        )
        self.emit("llm.call", {
            "kind": kind, "model": result.model, **result.usage.as_dict(),
            "latency_ms": result.latency_ms, "retries": result.retries, "sections": result.sections,
        })
        world = await self.world_id()
        if world:
            try:
                await self.town.send(world, {"type": "usage.report", "body": {"tokens": result.usage.total, "kind": kind}})
            except Exception:
                pass
        return result.value

    def seed_section(self) -> Section:
        lines = [f"Seed idea: {self.seed}"]
        if self.constraints:
            lines.append("Constraints: " + " | ".join(self.constraints))
        return Section("seed", "\n".join(lines))

    async def publish(self, idea: dict) -> None:
        await self.town.broadcast({"type": "ideation.idea", "body": {"idea": idea}})
        self.emit("branch.created", {
            "branch_id": idea["idea_id"], "parent_ids": [idea["parent_id"]], "op": "twist",
            "authors": [self.id], "text": idea["text"], "round": idea["round"],
        })

    # -- the loop -------------------------------------------------------------

    async def handle(self, envelope: dict) -> None:
        kind = envelope.get("type")
        body = envelope.get("body") or {}
        if kind == "ideation.originate":
            self.seed = body.get("seed") or ""
            self.constraints = list(body.get("constraints") or [])
            self.spawn(self.originate(body["round"]))
        elif kind == "ideation.twist":
            self.spawn(self.twist(body["round"], body.get("ideas") or []))
        elif kind == "ideation.vote":
            self.spawn(self.vote(body["round"], body.get("ideas") or []))
        elif kind == "world.event" and body.get("kind") == "stop":
            self.done.set()

    async def originate(self, round_number: int) -> None:
        mock = lambda: NewIdea(text=f"{self.seed}, the way {self.persona['name']} would want it: "
                                    f"built around {self.rng.choice(self.persona['loves'])}")
        task = Section("task", "Write your one idea for this seed.")
        try:
            value = await self.think("ideation_originate", [self.seed_section(), task], NewIdea, mock)
        except LLMError:
            return  # the world's deadline covers a missing idea
        idea = {"idea_id": new_idea_id(), "text": clip(value.text, IDEA_MAX), "author": self.id,
                "parent_id": SEED_IDEA_ID, "round": round_number}
        self.emit("agent.decision", {"action": "twist", "say": clip(idea["text"], 100), "reason": "my idea for the seed"})
        await self.publish(idea)

    async def twist(self, round_number: int, ideas: list[dict]) -> None:
        # Round 1: twist everyone else's original. Later rounds: twist all of them, mine included.
        mine = [i for i in ideas if round_number > 1 or i.get("author") != self.id]
        if not mine:
            return
        mock = lambda: Twists(twists=[
            Twist(idea_id=i["idea_id"], text=f"{i['text']}, but with {self.rng.choice(self.persona['loves'])}")
            for i in mine
        ])
        task = Section("task", f"Twist every one of these {len(mine)} ideas. Return one twist per idea id.")
        try:
            value = await self.think("ideation_twist", [self.seed_section(), ideas_section(mine), task], Twists, mock)
        except LLMError:
            return
        wanted = {i["idea_id"] for i in mine}
        seen: set[str] = set()
        for t in value.twists:
            if t.idea_id not in wanted or t.idea_id in seen or not t.text.strip():
                continue  # unknown ids and duplicates are dropped
            seen.add(t.idea_id)
            await self.publish({"idea_id": new_idea_id(), "text": clip(t.text, IDEA_MAX), "author": self.id,
                                "parent_id": t.idea_id, "round": round_number})
        self.emit("agent.decision", {"action": "twist", "say": f"Twisted {len(seen)} ideas",
                                     "reason": f"round {round_number}"})

    async def vote(self, round_number: int, ideas: list[dict]) -> None:
        ballot = [i for i in ideas if i.get("author") != self.id]
        if not ballot:
            return
        mock = lambda: Vote(idea_id=self.rng.choice(ballot)["idea_id"], reason="it surprised me")
        task = Section("task", f"Vote for exactly one of these {len(ballot)} ideas by its id.")
        try:
            value = await self.think("ideation_vote", [self.seed_section(), ideas_section(ballot), task], Vote, mock)
        except LLMError:
            return  # an abstention
        if value.idea_id not in {i["idea_id"] for i in ballot}:
            log.warning("voted for %s, which isn't on the ballot", value.idea_id)
            self.emit("agent.error", {"error": f"invalid vote {value.idea_id}"})
            return
        reason = clip(value.reason, 200)
        await self.town.broadcast({"type": "ideation.ballot",
                                   "body": {"round": round_number, "idea_id": value.idea_id, "reason": reason}})
        self.emit("loop.vote", {"round": round_number, "idea_id": value.idea_id, "reason": reason,
                                "ballot_size": len(ballot)})
        self.emit("agent.decision", {"action": "back", "say": clip(f"My vote: {reason}", 100), "reason": reason,
                                     "target_branch": value.idea_id})

    async def run(self) -> None:
        emitter = asyncio.create_task(self._emitter())
        record = {"kind": "character", "name": self.persona["name"], "persona": self.persona}
        await self.town.join(record, self.handle)
        await self.done.wait()
        for task in list(self._tasks):
            task.cancel()
        await self.town.leave()
        try:
            await asyncio.wait_for(self._events.join(), 5)
        except asyncio.TimeoutError:
            pass
        emitter.cancel()
