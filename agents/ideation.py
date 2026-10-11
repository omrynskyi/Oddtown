"""A character in the ideation voting loop (world/ideation.py), not the town.

character.py runs this instead of the town's Character when the world starts
the process with ODDTOWN_MODE=ideation_loop. Nobody dies or gets replaced in
this mode, so the agent count stays fixed for the counts to work.

It does three things, each one LLM call through the persona's own model:

    ideation.originate  write one idea from the seed, broadcast it
    ideation.twist      twist every idea handed over that isn't linked to me
                        (round 1: everyone else's original; later: all N), one call for all
    ideation.vote       vote for exactly one idea I'm not linked to

Between rounds the world retires one agent at random (or the one that crashed),
and the replacement is designed with the town's mechanics in agents/design.py:
the dying agent says its last words and picks three random survivors, one
proposes and two refine, and the last one asks the world to start the newcomer.
If the content check fails, the last designer refines once more to fix it, and
if it fails again the world falls back to an unused persona.

    ideation.retire          from the world: say last words, pick designers, leave
    ideation.replace_crashed from the world: an agent crashed, pick its designers
    replace.request, persona.draft   the design chain, between agents

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
from agents.brain import LastWords, clip, persona_text
from agents.design import DesignChain
from agents.llm import LLMError, Section
from agents.messages import AGENT_LAST_WORDS, PERSONA_DRAFT, REPLACE_REQUEST
from agents.schema import public_persona
from transport.adapter import DeadPeerError
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
        self.name = persona["name"]
        self.run_id = run_id
        self.town = town
        self.rng = random.Random(secrets.randbits(32))
        self.done = asyncio.Event()
        self.alive = True
        self.known: dict[str, dict] = {}  # id -> public persona of everyone seen alive
        self.dead: set[str] = set()
        self.selected: list[dict] = []  # the latest round's survivors, for a newcomer's briefing
        self._world_id: str | None = None
        self._tasks: set[asyncio.Task] = set()
        self._events: asyncio.Queue = asyncio.Queue()
        self.chain = DesignChain(self)
        # A newcomer missed the seed, so its designers hand it what the loop knows.
        briefing = persona.get("briefing") or {}
        self.seed = briefing.get("seed") or ""
        self.constraints: list[str] = list(briefing.get("constraints") or [])

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

    def emit(self, type: str, payload: dict, agent_id: str | None = None) -> None:
        self._events.put_nowait((type, payload, agent_id or self.id))

    async def _emitter(self) -> None:
        """One queue, so this agent's events reach the observer in order."""
        while True:
            type, payload, agent_id = await self._events.get()
            try:
                await aemit(self.run_id, type, payload, agent_id)
            finally:
                self._events.task_done()

    async def world_id(self) -> str | None:
        if self._world_id is None:
            worlds = await self.town.registry.search(kind="world")
            self._world_id = worlds[0]["id"] if worlds else None
        return self._world_id

    # -- what the design chain (agents/design.py) needs from its host --------

    async def living_peers(self) -> list[dict]:
        records = await self.town.characters()
        peers = [
            public_persona(r["persona"]) for r in records
            if isinstance(r.get("persona"), dict) and r["id"] not in self.dead
        ]
        self.known.update({p["id"]: p for p in peers})
        return peers

    async def send(self, to: str, type: str, body: dict, observe: bool = True) -> bool:
        try:
            await self.town.send(to, {"type": type, "body": body})
        except DeadPeerError:
            self.dead.add(to)
            return False
        if observe:
            self.emit(type, {"to": to, **body})
        return True

    def briefing(self) -> dict:
        branches = [
            {"branch_id": i["idea_id"], "parent_ids": [i.get("parent_id")], "op": "twist",
             "authors": [i["author"]], "text": i["text"]}
            for i in self.selected
        ]
        return {"seed": self.seed, "constraints": self.constraints, "branches": branches, "endorsements": {}}

    def remember(self, text: str, kind: str) -> None:
        pass  # loop agents keep no memory stream

    def spend_design_step(self) -> None:
        pass  # nobody's life runs out in the loop; the world decides who retires

    def enqueue_design(self, env: dict) -> None:
        self.spawn(self.chain.design(env))

    async def on_design_failed(self, persona: dict, body: dict, check: str, reason: str) -> None:
        """Refine once more to fix what the check flagged. If that fails too, the world falls back."""
        if not body.get("retried"):
            feedback = f"The {check} check rejected this persona: {clip(reason, 300)}. Fix exactly that and keep the rest."
            retry = {**body, "draft": persona, "retried": True, "step": len(body["order"]) - 1}
            try:
                await self.chain.refine(retry, feedback=feedback)
                return
            except LLMError:
                pass
        world = await self.world_id()
        if world:
            await self.send(world, "ideation.replace_failed", {"replaces": body["replaces"], "reason": clip(reason, 300)}, observe=False)

    async def think(self, kind: str, prompt: tuple, out, mock):
        system, user = prompt
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
        return result

    async def ask(self, kind: str, user: list[Section], out, mock):
        """One call with the loop's rules and this persona, returning just the value."""
        system = [Section("rules", RULES), Section("persona", persona_text(self.persona))]
        return (await self.think(kind, (system, user), out, mock)).value

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
            self.catch_up(body)
            self.spawn(self.twist(body["round"], body.get("ideas") or []))
        elif kind == "ideation.vote":
            self.catch_up(body)
            self.spawn(self.vote(body["round"], body.get("ideas") or []))
        elif kind == "ideation.result":
            self.selected = list(body.get("selected") or [])
        elif kind == "ideation.retire":
            self.spawn(self.retire(body))
        elif kind == "ideation.replace_crashed":
            self.spawn(self.replace_crashed(body))
        elif kind in (REPLACE_REQUEST, PERSONA_DRAFT):
            if kind == PERSONA_DRAFT:
                self.chain.drafts_seen.add(body.get("replaces"))
            self.spawn(self.chain.design(envelope))
        elif kind == AGENT_LAST_WORDS:
            self.dead.add(envelope.get("from"))
        elif kind == "world.event" and body.get("kind") == "stop":
            self.done.set()

    def catch_up(self, body: dict) -> None:
        if body.get("seed"):
            self.seed = body["seed"]
        if body.get("constraints") is not None:
            self.constraints = list(body["constraints"])

    # -- dying and replacing, between rounds ----------------------------------

    async def retire(self, body: dict) -> None:
        """The world drew this agent to retire after a round: last words, then pick the designers."""
        if not self.alive:
            return
        peers = await self.living_peers()
        mock = lambda: LastWords(text=f"{self.name} signs off. Keep twisting toward {self.rng.choice(self.persona['loves'])}.")
        task = Section("task", "You are retiring from the loop now. Write your last words to the others: one final "
                               "pitch, in your own voice, under 280 characters.")
        ideas = [Section("survivors", "\n".join(f"- {i['text']}" for i in self.selected))] if self.selected else []
        try:
            words = (await self.ask("last_words", [self.seed_section(), *ideas, task], LastWords, mock)).text
        except LLMError:
            words = f"{self.name} is gone."
        self.alive = False
        words = clip(words, 280)
        self.emit("agent.last_words", {"text": words})
        await self.town.broadcast({"type": AGENT_LAST_WORDS, "body": {"text": words}})
        picked = await self.chain.pick_designers(exclude={self.id}, include_me=False, peers=peers)
        generation = self.persona.get("lineage", {}).get("generation", 0) + 1
        await self.chain.request_replacement(self.id, self.name, picked, generation)
        self.emit("agent.died", {"picked": picked})
        self.done.set()

    async def replace_crashed(self, body: dict) -> None:
        """Another agent crashed mid-round. The world asked this one to pick its designers, as a town peer would."""
        crashed = body["replaces"]
        self.dead.add(crashed)
        picked = await self.chain.pick_designers(exclude={crashed}, include_me=True)
        await self.chain.request_replacement(crashed, body.get("replaces_name") or crashed, picked, body.get("generation", 1))
        self.emit("agent.died", {"picked": picked, "noticed_by": self.id}, agent_id=crashed)

    async def originate(self, round_number: int) -> None:
        mock = lambda: NewIdea(text=f"{self.seed}, the way {self.persona['name']} would want it: "
                                    f"built around {self.rng.choice(self.persona['loves'])}")
        task = Section("task", "Write your one idea for this seed.")
        try:
            value = await self.ask("ideation_originate", [self.seed_section(), task], NewIdea, mock)
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
            value = await self.ask("ideation_twist", [self.seed_section(), ideas_section(mine), task], Twists, mock)
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
            value = await self.ask("ideation_vote", [self.seed_section(), ideas_section(ballot), task], Vote, mock)
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
