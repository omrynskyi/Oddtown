"""One character, one process. The world service starts it as `python -m agents.character`.

It reads AGENT_ID, PERSONA_FILE and RUN_ID from the environment (see
world/launcher.py), joins the town through transport.town.Town, and then:

- Batches its inbox. A waking message (share, pitch, merge offer, replacement
  work, world event) starts a 3 second window, then one decision call answers
  everything that piled up. With an empty inbox, an idle timer of 20 to 40
  seconds makes it start something on its own.
- Spends one action of its lifespan per decision or design step, and dies when
  its actions or its wall-clock time run out. Dying means last words, three
  random survivors picked to design the replacement, `agent.died`, then leave.
- Designs replacements when picked: propose, refine, or refine and birth.
- Withdraws its own branches that nobody liked: once everyone it showed one to
  has turned it down and nobody backs it, a `branch.withdraw` broadcast makes
  every character compost it. It does the same past BRANCH_BUDGET unbacked
  branches (the oldest go), and for its unbacked branches when it dies.
- Notices crashes. If its message to a peer bounces, it does that peer's
  replacement pick, since the peer never got to.
- Reports every LLM call's tokens to the world service (`usage.report`) and to
  the observer (`llm.call`), and everything it does to the observer through
  observer.client, in the shapes observer/events.py checks.

Nothing here decides for anyone else. Every choice comes from this character's
own LLM call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
import uuid
from collections import deque
from pathlib import Path

from pydantic import ValidationError

from agents import brain, config, llm, replace
from agents.brain import Decision, Reply, View, clip
from agents.llm import LLMError
from agents.memory import MemoryStream
from agents.social import Ledger, quote
from agents.messages import (
    AGENT_HELLO, AGENT_LAST_WORDS, ANSWERS, ENDORSE, HUMAN_REQUEST, IDEA_BUILT, IDEA_PITCH, IDEA_REACTION, IDEA_SHARE,
    MERGE_PROPOSE, MERGE_RESPONSE, OBJECTION, PERSONA_DRAFT, PITCH_RESPONSE, REPLACE_REQUEST, REST,
    SPAWN_REQUEST, SPAWN_RESULT, UNENDORSE, USAGE_REPORT, WITHDRAW, WORLD_EVENT, envelope,
)
from agents.schema import new_branch_id, now_iso, public_persona, validate_persona
from observer.client import aemit
from transport.adapter import DeadPeerError
from transport.town import Town

log = logging.getLogger("character")

NOT_IN_REGISTRY = "not in the registry"  # transport.town's reason when someone already removed the record

PERSONA_DEFAULTS = {
    "one_liner": "a newcomer", "worldview": "Ideas should surprise people.", "loves": ["surprises"],
    "hates": ["sameness"], "red_line": "Never backs a boring idea", "voice": "Plain and direct",
    "traits": {axis: 0.0 for axis in ("time_horizon", "tech_attitude", "scale", "sociality", "risk", "rationality")},
    "lineage": {"generation": 0, "designed_by": [], "replaces": None},
}


def same_idea(a: str | None, b: str | None) -> bool:
    """Two texts that are mostly the same words, like a reply's idea repeated as the turn's twist."""
    wa, wb = set(re.findall(r"[a-z]+", (a or "").lower())), set(re.findall(r"[a-z]+", (b or "").lower()))
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) > 0.8


# Why an author withdrew a branch, as its memory puts it.
WITHDRAWN = {
    "unliked": "everyone you showed it to turned it down",
    "budget": f"you had more than {config.BRANCH_BUDGET} unbacked branches",
    "died": "you died with nobody backing it",
}


def load_persona(raw: dict) -> dict:
    """The schema check, but a bad persona file never stops a character from living."""
    try:
        return validate_persona(raw).model_dump()
    except ValidationError as exc:
        log.warning("persona %s failed the schema check, filling defaults: %s", raw.get("id"), exc)
        persona = {**PERSONA_DEFAULTS, **raw}
        persona.setdefault("name", persona["id"])
        persona.setdefault("model", config.DEFAULT_MODEL)
        return persona


class Character:
    def __init__(self, persona: dict, run_id: str, town: Town):
        self.full_persona = load_persona(persona)
        self.persona = public_persona(self.full_persona)
        self.id = self.persona["id"]
        self.name = self.persona["name"]
        self.run_id = run_id
        self.town = town
        self.rng = random.Random(f"{run_id}:{self.id}")

        self.memory = MemoryStream()
        self.social = Ledger()
        self.heard_from: set[str] = set()  # peers who sent anything since the last turn
        self.seed: str | None = None
        self.constraints: list[str] = []
        self.branches: dict[str, dict] = {}
        self.endorsements: dict[str, list[str]] = {}  # public: agent id -> branch ids it backs
        self.my_endorsed: list[str] = []
        self.retired: set[str] = set()  # branches their authors withdrew, composted until someone backs them
        self.verdicts: dict[str, dict[str, bool | None]] = {}  # our branch id -> who we showed it -> liked it, None until they answer
        self.agenda = ""
        self.known: dict[str, dict] = {}  # last seen persona for every peer, kept after they die
        self.dead: set[str] = set()

        self.inbox: list[dict] = []  # waking messages the next decision call answers
        self.built_this_turn: list[dict] = []  # branches this turn's replies made
        self.designs: deque[dict] = deque()  # replacement work, done before the next decision
        self.drafts_seen: set[str] = set()  # replacements whose draft reached us
        self.pending_reviews: list[dict] = []  # newcomers waiting on a human after a failed content check
        self.news = False  # a world event arrived since the last turn
        self.wake = asyncio.Event()
        self.done = asyncio.Event()
        self.alive = True
        self.stopping = False

        generation = self.persona.get("lineage", {}).get("generation", 0)
        share = 1.0 if generation > 0 else self.rng.uniform(*config.STAGGER)
        max_actions = (
            config.MAX_ACTIONS if "ODDTOWN_MAX_ACTIONS" in os.environ
            else self.persona.get("lifespan", {}).get("max_actions", config.MAX_ACTIONS)
        )
        self.life_max = max(1, round(max_actions * share))
        self.life_left = self.life_max
        self.lifespan_s = config.scaled(config.LIFESPAN_S) * share
        self.born: float | None = None  # the lifespan clock starts with the seed, or at birth for newcomers

        self.last_support_change = time.monotonic()
        self.rested = False
        self._world_id: str | None = None
        self._events: asyncio.Queue = asyncio.Queue()
        self._tasks: set[asyncio.Task] = set()

    # -- running ----------------------------------------------------------

    async def run(self) -> None:
        self.spawn(self._emitter())
        record = {
            "kind": "character",
            "name": self.name,
            "persona": self.persona,
            "traits": self.persona.get("traits"),
            "lineage": self.persona.get("lineage"),
        }
        await self.town.join(record, self.on_envelope)
        replaces = self.persona.get("lineage", {}).get("replaces")
        await self.broadcast(AGENT_HELLO, {"name": self.name, "one_liner": self.persona["one_liner"], "replaces": replaces})
        self.emit(AGENT_HELLO, {"name": self.name, "replaces": replaces})
        if self.full_persona.get("briefing"):
            self.load_briefing(self.full_persona["briefing"])
        self.spawn(self.loop())
        await self.done.wait()
        await self.finish()

    async def finish(self) -> None:
        self.alive = False
        try:
            await asyncio.wait_for(self._events.join(), timeout=5)
        except asyncio.TimeoutError:
            log.warning("observer events still queued at exit")
        try:
            await self.town.leave()
        except Exception:
            log.exception("leaving the town failed")

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(self._log_failure)
        return task

    def _log_failure(self, task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception():
            log.error("task failed", exc_info=task.exception())
            self.emit("agent.error", {"error": repr(task.exception())})

    def start_clock(self) -> None:
        if self.born is None:
            self.born = time.monotonic()

    def seconds_left(self) -> float:
        if self.born is None:
            return self.lifespan_s
        return self.lifespan_s - (time.monotonic() - self.born)

    def out_of_life(self) -> bool:
        return self.life_left <= 0 or (self.born is not None and self.seconds_left() <= 0)

    def idle_timeout(self) -> float | None:
        if self.seed is None:
            return None  # nothing to talk about before the seed
        idle = config.scaled(self.rng.uniform(*config.IDLE_S))
        return max(0.05, min(idle, self.seconds_left()))

    async def loop(self) -> None:
        while self.alive:
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=self.idle_timeout())
                woke = True
            except asyncio.TimeoutError:
                woke = False
            if not self.alive:
                break
            if woke:
                await asyncio.sleep(config.scaled(config.BATCH_WINDOW_S))
                self.wake.clear()
            try:
                await self.step(idle=not woke)
            except Exception:
                log.exception("turn failed")
                self.emit("agent.error", {"error": "turn failed, see the agent log"})

    async def step(self, idle: bool) -> None:
        while self.designs and self.alive:
            await self.design(self.designs.popleft())
        if not self.alive:
            return
        if self.out_of_life():
            await self.die()
            return
        if self.seed is None or (not idle and not self.inbox and not self.news):
            return
        await self.take_turn(idle)
        if self.alive and self.memory.wants_reflection():
            await self.reflect()
        if self.alive and self.out_of_life():
            await self.die()
        elif self.alive:
            await self.maybe_rest()

    # -- what arrives -----------------------------------------------------

    async def on_envelope(self, env: dict) -> None:
        """Runs once per incoming message. Only updates state; the turn loop does the thinking."""
        if not self.alive:
            return
        kind = env.get("type")
        body = env.get("body") or {}
        sender = env.get("from")
        who = self.name_of(sender)

        if kind == WORLD_EVENT:
            self.on_world_event(body)
        elif kind in (IDEA_SHARE, IDEA_PITCH, MERGE_PROPOSE):
            branch = self.learn(body.get("branch"))
            bid = branch["branch_id"] if branch else None
            self.heard_from.add(sender)
            if kind == IDEA_SHARE:
                self.memory.add(f'{who} shared {self.ref(branch)} with you. Note: "{clip(body.get("note"), 80)}"', "heard_idea", about=(sender, bid))
                self.tie_note(sender, f"they showed you {self.ref(branch)}: {quote(body.get('note'))}")
            elif kind == IDEA_PITCH:
                self.memory.add(f'{who} pitched {self.ref(branch)} to you: "{clip(body.get("note"), 80)}"', "pitch", about=(sender, bid))
                self.tie_note(sender, f"they pitched {self.ref(branch)} to you: {quote(body.get('note'))}")
            else:
                a, b = (body.get("branch_ids") or ["?", "?"])[:2]
                self.memory.add(f'{who} offered to merge {a} and {b} into: "{clip(body.get("text"), 80)}"', "merge", about=(sender, a, b))
                self.tie_note(sender, f'they offered to merge {a} and {b} into "{clip(body.get("text"), 100)}": {quote(body.get("note"))}')
            self.inbox.append(env)
            self.wake.set()
        elif kind in (REPLACE_REQUEST, PERSONA_DRAFT):
            self.designs.append(env)
            if kind == PERSONA_DRAFT:
                self.drafts_seen.add(body.get("replaces"))
            self.wake.set()
        elif kind == IDEA_REACTION:
            built = self.learn(body.get("branch"))
            bid = body.get("branch_id")
            verdict = "accepted it" if body.get("answer") == "accept" else "rejected it"
            if built:
                verdict += f" and built on it as {self.ref(built)}"
                self.social.tie(sender).they_built += 1
            self.social.answer(sender, mine=True, yes=body.get("answer") == "accept")
            self.heard_verdict(bid, sender, body.get("answer") == "accept")
            self.memory.add(f'{who} reacted to your {bid}: {verdict}, "{clip(body.get("text"), 100)}"', "reaction", about=(sender, bid, built and built["branch_id"]))
            self.tie_note(sender, f"they {verdict.replace('it', 'your ' + str(bid), 1)}: {quote(body.get('text'))}")
            self.answered(sender)
        elif kind == PITCH_RESPONSE:
            built = self.learn(body.get("branch"))
            bid = body.get("branch_id")
            verdict = "persuaded" if body.get("persuaded") else "not persuaded"
            if built:
                verdict += f", and built on it as {self.ref(built)}"
                self.social.tie(sender).they_built += 1
            self.social.answer(sender, mine=True, yes=bool(body.get("persuaded")))
            self.heard_verdict(bid, sender, bool(body.get("persuaded")))
            self.memory.add(f'{who} was {verdict} by your pitch for {bid}: "{clip(body.get("text"), 100)}"', "reaction", about=(sender, bid, built and built["branch_id"]))
            self.tie_note(sender, f"your pitch for {bid}: they were {verdict}: {quote(body.get('text'))}")
            self.answered(sender)
        elif kind == MERGE_RESPONSE:
            branch = self.learn(body.get("branch"))
            answer = body.get("answer")
            result = f", making {branch['branch_id']}" if branch else ""
            self.social.answer(sender, mine=True, yes=answer in ("accept", "counter"))
            self.memory.add(f'{who} answered your merge offer: {answer}{result}. "{clip(body.get("text"), 100)}"', "merge", about=(sender, branch and branch["branch_id"]))
            self.tie_note(sender, f"they answered your merge offer: {answer}{result}: {quote(body.get('text'))}")
            self.answered(sender)
        elif kind == IDEA_BUILT:
            branch = self.learn(body.get("branch"))
            if branch:
                parent = body.get("parent_id")
                self.social.tie(sender).they_built += 1
                self.memory.add(f"{who} built on your {parent}: {self.ref(branch)}", "heard_idea", about=(sender, parent, branch["branch_id"]))
                self.tie_note(sender, f"they built on your {parent}: {self.ref(branch)}")
                self.answered(sender)
        elif kind == ENDORSE:
            branch = self.learn(body.get("branch"))
            branch_id = branch["branch_id"] if branch else body.get("branch_id")
            if branch_id:
                self.public_endorse(sender, branch_id)
                self.memory.add(f"{who} now backs {self.ref(self.branches.get(branch_id)) or branch_id}", "endorsement", about=(sender, branch_id))
                if self.id in (self.branches.get(branch_id) or {}).get("authors", []):
                    self.tie_note(sender, f"they publicly backed your {branch_id}")
        elif kind == UNENDORSE:
            branch_id = body.get("branch_id")
            if branch_id:
                self.public_unendorse(sender, branch_id)
                reason = f': "{clip(body.get("reason"), 80)}"' if body.get("reason") else ""
                self.memory.add(f"{who} dropped {branch_id}{reason}", "endorsement", about=(sender, branch_id))
        elif kind == WITHDRAW:
            branch_id = body.get("branch_id")
            if branch_id and not self.backers(branch_id):  # backing it brings it back, so a late withdraw changes nothing
                self.retired.add(branch_id)
        elif kind == OBJECTION:
            branch_id = body.get("branch_id")
            self.memory.add(f'{who} objected to {branch_id}: "{clip(body.get("reason"), 100)}"', "objection", about=(sender, branch_id))
            if self.id in (self.branches.get(branch_id) or {}).get("authors", []):
                self.tie_note(sender, f"they publicly objected to your {branch_id}: {quote(body.get('reason'))}")
        elif kind == AGENT_HELLO:
            replaces = body.get("replaces")
            note = f", replacing {self.name_of(replaces)}" if replaces else ""
            self.memory.add(f"{body.get('name') or who} joined the town{note}: {clip(body.get('one_liner'), 100)}", "hello")
        elif kind == AGENT_LAST_WORDS:
            self.mark_dead(sender)
            self.memory.add(f'{who} died. Last words: "{clip(body.get("text"), 150)}"', "death", about=(sender,))
        elif kind == SPAWN_RESULT:
            if not body.get("ok"):
                log.warning("the world couldn't start a newcomer: %s", body.get("error"))

    def on_world_event(self, body: dict) -> None:
        kind, text = body.get("kind"), body.get("text") or ""
        if kind == "stop":
            self.stopping = True
            self.alive = False
            self.wake.set()
            self.done.set()
            return
        if kind == "seed":
            self.seed = text
            self.learn(body.get("branch"))
            self.memory.add(f'The world dropped in the seed idea: "{clip(text, 150)}"', "world")
            self.start_clock()
            self.last_support_change = time.monotonic()
        elif kind == "constraint":
            self.constraints.append(text)
            self.memory.add(f'The world added a constraint: "{clip(text, 150)}"', "world")
        elif kind == "feedback":
            self.memory.add(f'Customer feedback from the world: "{clip(text, 150)}"', "world")
        elif kind == "human.answer":
            self.memory.add(f'A human answered a question to the town: "{clip(text, 150)}"', "human")
            self.on_human_answer(text)
        else:
            return
        self.news = True
        self.wake.set()

    def load_briefing(self, briefing: dict) -> None:
        """A newcomer missed the seed, so its designers hand it what the town knows."""
        self.seed = briefing.get("seed")
        self.constraints = list(briefing.get("constraints") or [])
        for branch in briefing.get("branches") or []:
            self.learn(branch)
        for agent_id, branch_ids in (briefing.get("endorsements") or {}).items():
            self.endorsements[agent_id] = list(branch_ids)[-config.MAX_ENDORSEMENTS:]
        replaces = self.persona.get("lineage", {}).get("replaces")
        self.memory.add(
            f'You were just born to replace {self.name_of(replaces)}. The town is twisting the seed idea "{clip(self.seed, 150)}".',
            "replacement",
        )
        self.start_clock()
        self.news = True
        delay = config.scaled(self.rng.uniform(*config.NEWCOMER_FIRST_TURN_S))
        asyncio.get_running_loop().call_later(delay, self.wake.set)

    # -- town state -------------------------------------------------------

    def learn(self, branch: dict | None) -> dict | None:
        if not isinstance(branch, dict) or not branch.get("branch_id") or not branch.get("text"):
            return None
        self.branches.setdefault(branch["branch_id"], dict(branch))
        for author in branch.get("authors") or []:
            if author != self.id:
                self.social.their_idea(author, branch["branch_id"])
        return self.branches[branch["branch_id"]]

    def tie_note(self, peer: str | None, line: str) -> None:
        self.social.note(peer, self.memory.step, line)

    def answered(self, sender: str | None) -> None:
        """An answer or a credit came back. With threads on, it wakes us so the exchange can continue."""
        self.heard_from.add(sender)
        if config.on("threads"):
            self.news = True
            self.wake.set()

    def ref(self, branch: dict | None) -> str:
        """How memories mention a branch: its id and a short excerpt. The full text is in the branches section."""
        if not branch:
            return "a branch"
        return f'{branch["branch_id"]} ("{clip(branch["text"], 60)}")'

    def name_of(self, agent_id: str | None) -> str:
        if agent_id == self.id:
            return "you"
        if agent_id == "world":
            return "the world"
        return (self.known.get(agent_id) or {}).get("name") or agent_id or "someone"

    def support_changed(self) -> None:
        self.last_support_change = time.monotonic()
        self.rested = False

    def public_endorse(self, agent_id: str, branch_id: str) -> None:
        backed = self.endorsements.setdefault(agent_id, [])
        if branch_id in backed:
            return
        backed.append(branch_id)
        del backed[: max(0, len(backed) - config.MAX_ENDORSEMENTS)]
        self.retired.discard(branch_id)  # backing a withdrawn branch brings it back
        self.support_changed()

    def public_unendorse(self, agent_id: str, branch_id: str) -> None:
        backed = self.endorsements.get(agent_id, [])
        if branch_id in backed:
            backed.remove(branch_id)
            self.support_changed()

    def backers(self, branch_id: str) -> list[str]:
        return [a for a, ids in self.endorsements.items() if branch_id in ids and a not in self.dead]

    def composted(self, branch_id: str) -> bool:
        """Its author withdrew it and nobody living backs it. Same as View.composted."""
        return branch_id in self.retired and not self.backers(branch_id)

    def my_unbacked(self) -> list[str]:
        """Our own branches that nobody living backs and that are still in play, oldest first. Same as View.my_unbacked."""
        mine = [b for b, branch in self.branches.items() if self.id in (branch.get("authors") or [])]
        return [b for b in mine if not self.backers(b) and not self.composted(b)]

    def mark_dead(self, agent_id: str) -> None:
        self.dead.add(agent_id)
        if self.endorsements.pop(agent_id, None):
            self.support_changed()

    async def living_peers(self) -> list[dict]:
        records = await self.town.characters()
        peers = [
            public_persona(r["persona"]) for r in records
            if isinstance(r.get("persona"), dict) and r["id"] not in self.dead
        ]
        self.known.update({p["id"]: p for p in peers})
        return peers

    async def world_id(self) -> str | None:
        if self._world_id is None:
            worlds = await self.town.registry.search(kind="world")
            self._world_id = worlds[0]["id"] if worlds else None
        return self._world_id

    def briefing(self) -> dict:
        living = set(self.known) - self.dead | {self.id}
        support = {b: sum(b in ids for a, ids in self.endorsements.items() if a in living) for b in self.branches}
        in_play = [b for b in self.branches.values() if not self.composted(b["branch_id"])]
        top = sorted(in_play, key=lambda b: (b.get("op") != "seed", -support.get(b["branch_id"], 0)))[:8]
        return {
            "seed": self.seed,
            "constraints": self.constraints,
            "branches": top,
            "endorsements": {a: ids for a, ids in self.endorsements.items() if a in living and ids},
        }

    def focus(self, inbox: list[dict]) -> tuple[set[str], set[str], str]:
        """Who and what this turn is about: peers who wrote since the last turn, the branches in the inbox,
        the ones we back and the agenda. Retrieval scores memories by overlap with it."""
        peers = {p for p in self.heard_from if p}
        branches = set(self.my_endorsed)
        texts = [self.agenda]
        for env in inbox:
            body = env.get("body") or {}
            peers.add(env.get("from"))
            branches.add((body.get("branch") or {}).get("branch_id"))
            branches.update(body.get("branch_ids") or [])
            texts += [body.get("note") or "", body.get("text") or "", (body.get("branch") or {}).get("text") or ""]
        branches.update(re.findall(r"br_[0-9a-f]{6}", self.agenda))
        return {p for p in peers if p}, {b for b in branches if b}, " ".join(texts)

    def view(self, peers: list[dict], inbox: list[dict], idle: bool) -> View:
        to_score = self.memory.unscored()
        focus_peers, focus_branches, query = self.focus(inbox)
        exclude = {o.id for o in to_score}
        if config.on("relevance"):
            memories = self.memory.retrieve(exclude=exclude, focus=focus_peers | focus_branches, query=query)
        else:
            memories = self.memory.retrieve(exclude=exclude)
        return View(
            me=self.persona,
            seed=self.seed,
            constraints=self.constraints,
            life_left=self.life_left,
            life_max=self.life_max,
            seconds_left=max(0.0, self.seconds_left()),
            agenda=self.agenda,
            my_endorsed=list(self.my_endorsed),
            peers=peers,
            endorsements={a: list(ids) for a, ids in self.endorsements.items()},
            branches=self.branches,
            memories=memories,
            inbox=inbox,
            to_score=to_score,
            idle=idle,
            ledger=self.social,
            focus=focus_peers,
            retired=set(self.retired),
        )

    # -- talking ----------------------------------------------------------

    async def send(self, to: str, type: str, body: dict, observe: bool = True) -> bool:
        """Point to point. Every message between characters is also posted to the observer.

        Messages to the world service aren't, since the world emits its own events for them.
        """
        try:
            await self.town.send(to, envelope(type, body))
        except DeadPeerError as exc:
            await self.on_dead_peer(exc.peer_id, exc.reason)
            return False
        if observe:
            self.emit(type, {"to": to, **body})
        return True

    async def broadcast(self, type: str, body: dict) -> None:
        for peer in await self.town.broadcast(envelope(type, body)):
            await self.on_dead_peer(peer, "bounced")

    async def on_dead_peer(self, peer_id: str, reason: str) -> None:
        """A send bounced. If the peer never said goodbye, it crashed, so do its replacement pick."""
        if peer_id in self.dead or peer_id == await self.world_id():
            return
        self.mark_dead(peer_id)
        if reason == NOT_IN_REGISTRY or not self.alive:
            return  # it left normally, or another peer already noticed
        name = self.name_of(peer_id)
        self.memory.add(f"{name} stopped answering. They must have crashed.", "death")
        self.emit("agent.bounced", {"noticed_by": self.id}, agent_id=peer_id)
        generation = (self.known.get(peer_id) or {}).get("lineage", {}).get("generation", 0) + 1
        picked = await self.pick_designers(exclude={peer_id}, include_me=True)
        await self.request_replacement(peer_id, name, picked, generation)
        self.emit("agent.died", {"picked": picked, "noticed_by": self.id}, agent_id=peer_id)

    # -- observer ---------------------------------------------------------

    def emit(self, type: str, payload: dict, agent_id: str | None = None) -> None:
        self._events.put_nowait((type, payload, agent_id or self.id))

    async def _emitter(self) -> None:
        """One queue, so this character's events reach the observer in the order they happened."""
        while True:
            type, payload, agent_id = await self._events.get()
            try:
                await aemit(self.run_id, type, payload, agent_id)
            finally:
                self._events.task_done()

    # -- thinking ---------------------------------------------------------

    async def think(self, kind: str, prompt: tuple, out, mock, counts: dict | None = None):
        system, user = prompt
        try:
            result = await llm.call_json(kind=kind, model=self.persona["model"], system=system, user=user, out=out, mock=mock)
        except LLMError as exc:
            log.warning("%s call failed: %s", kind, exc)
            self.emit("llm.error", {"kind": kind, "error": str(exc)})
            raise
        self.emit("llm.call", {
            "kind": kind, "model": result.model, **result.usage.as_dict(), "latency_ms": result.latency_ms,
            "retries": result.retries, "sections": result.sections, "counts": counts or {},
        })
        world = await self.world_id()
        if world:
            await self.send_quietly(world, USAGE_REPORT, {"tokens": result.usage.total, "kind": kind, **result.usage.as_dict()})
        return result

    async def send_quietly(self, to: str, type: str, body: dict) -> None:
        """For bookkeeping messages the observer doesn't need to see."""
        try:
            await self.town.send(to, envelope(type, body))
        except DeadPeerError:
            pass

    # -- a turn -----------------------------------------------------------

    async def take_turn(self, idle: bool) -> None:
        await self.compost_unliked()
        inbox, self.inbox = self.inbox, []
        self.news = False
        peers = await self.living_peers()
        view = self.view(peers, inbox, idle)
        counts = {
            "memories": len(view.memories), "new_obs": len(view.to_score), "waking": len(inbox),
            "branches": len(brain.pick_branches(view)), "idle": idle,
        }
        self.heard_from = set()
        try:
            result = await self.think(
                "decision", brain.decision_prompt(view), brain.decision_model(), lambda: brain.mock_decision(view, self.rng), counts
            )
        except LLMError:
            return
        self.memory.step += 1
        self.life_left -= 1
        decision = result.value
        for score in decision.importance:
            self.memory.score(score.obs_id, score.importance)
        if view.to_score:
            self.memory.settle_through(view.to_score[-1].seq)
        await self.apply(decision, view, inbox)

    def resolve_peer(self, value: str | None, peers: list[dict]) -> str | None:
        if not value:
            return None
        for p in peers:
            if value == p["id"] or value.strip().lower() == p["name"].lower():
                return p["id"]
        return None

    async def apply(self, d: Decision, view: View, inbox: list[dict]) -> None:
        peers = view.peers
        peer_ids = [p["id"] for p in peers]
        action, target = d.action, d.target_branch if d.target_branch in self.branches else None
        send_to = self.resolve_peer(d.send_to, peers)
        if action in ("twist", "merge", "pitch") and send_to is None and peer_ids:
            send_to = self.rng.choice(peer_ids)

        # Fall back to something valid rather than lose the turn.
        if action == "twist" and target is None:
            target = next((b for b, br in self.branches.items() if br.get("op") == "seed"), None)
        other = d.other_branch if d.other_branch in self.branches and d.other_branch != target else None
        if (action in ("back", "trash", "pitch") and target is None) or (action == "merge" and (target is None or other is None)):
            action = "ignore"
        if action in ("twist", "merge") and not (d.text or "").strip():
            action = "ignore"
        if action in ("twist", "merge", "pitch") and send_to is None:
            action = "back" if action == "pitch" and target else ("ignore" if action != "twist" else action)

        self.emit("agent.decision", {
            "action": action, "say": clip(d.say, config.SAY_MAX) or "…", "reason": clip(d.reason, 300),
            "target_branch": target, "send_to": send_to, "memory_ids": [m.id for m in view.memories],
            "life_left": self.life_left,
        })

        by_msg = {env.get("msg_id"): env for env in inbox}
        self.built_this_turn = []
        for reply in d.replies:
            if reply.msg_id in by_msg:
                await self.reply(by_msg.pop(reply.msg_id), reply)

        repeat = next((b for b in self.built_this_turn if same_idea(b["text"], d.text)), None) if action == "twist" else None
        if repeat:
            # The twist repeats an idea a reply already made into a branch, so show that branch instead.
            if send_to and send_to not in repeat.get("_sent_to", ()):
                self.tie_note(send_to, f"you showed them {self.ref(repeat)}: {quote(d.note)}")
                self.showed(repeat["branch_id"], send_to)
                await self.send(send_to, IDEA_SHARE, {"branch": {k: v for k, v in repeat.items() if k != "_sent_to"}, "note": clip(d.note, 200)})
        elif action == "twist":
            branch = self.new_branch("twist", [target], [self.id], d.text)
            self.memory.add(f"You twisted {target} into {self.ref(branch)}", "action", about=(target, branch["branch_id"], send_to))
            await self.keep_budget()
            await self.credit(target, branch, skip=send_to)
            if send_to:
                self.tie_note(send_to, f"you showed them {self.ref(branch)}: {quote(d.note)}")
                self.showed(branch["branch_id"], send_to)
                await self.send(send_to, IDEA_SHARE, {"branch": branch, "note": clip(d.note, 200)})
        elif action == "merge":
            proposal = {"proposal_id": f"mp_{uuid.uuid4().hex[:8]}", "branch_ids": [target, other], "text": clip(d.text, config.BRANCH_TEXT_MAX), "note": clip(d.note, 200)}
            self.memory.add(f"You offered {self.name_of(send_to)} a merge of {target} and {other}", "action", about=(send_to, target, other))
            self.tie_note(send_to, f'you offered to merge {target} and {other} into "{clip(d.text, 100)}": {quote(d.note)}')
            await self.send(send_to, MERGE_PROPOSE, proposal)
        public = d.note if config.on("think") and (d.note or "").strip() else d.reason  # reason is private thinking
        if action == "back":
            await self.endorse(target, public)
        elif action == "trash":
            await self.trash(target, public)
        elif action == "pitch":
            self.memory.add(f"You pitched {target} to {self.name_of(send_to)}", "action", about=(send_to, target))
            self.tie_note(send_to, f"you pitched {self.ref(self.branches[target])} to them: {quote(d.note)}")
            self.showed(target, send_to)
            await self.send(send_to, IDEA_PITCH, {"branch": self.branches[target], "note": clip(d.note, 200)})

        for branch_id in d.unendorse:
            if branch_id in self.my_endorsed:
                await self.unendorse(branch_id, "")
        for branch_id in d.endorse:
            if branch_id in self.branches:
                await self.endorse(branch_id, public)

        if d.flag_human and d.flag_human.branch_id in self.branches:
            world = await self.world_id()
            if world:
                flag = d.flag_human
                body = {"kind": flag.kind, "branch_id": flag.branch_id, "branch": self.branches[flag.branch_id], "claim": clip(flag.claim, 300)}
                await self.send(world, HUMAN_REQUEST, body, observe=False)
                self.memory.add(f"You asked a human to rule on {flag.branch_id}: {clip(flag.claim, 100)}", "action")

    async def credit(self, parent_id: str | None, branch: dict, skip: str | None = None) -> None:
        """We built on someone else's branch. With credit on, its living authors hear about it."""
        parent = self.branches.get(parent_id) or {}
        for author in parent.get("authors") or []:
            if author == self.id or author in self.dead or author not in self.known:
                continue  # the seed's author is the world, not a character
            self.social.tie(author).you_built += 1
            if author == skip:
                continue  # they're about to get the branch itself
            self.tie_note(author, f"you built on their {parent_id}: {self.ref(branch)}")
            if config.on("credit"):
                await self.send(author, IDEA_BUILT, {"branch": branch, "parent_id": parent_id})

    def new_branch(self, op: str, parents: list[str], authors: list[str], text: str) -> dict:
        branch = {
            "branch_id": new_branch_id(),
            "parent_ids": parents,
            "op": op,
            "author": authors[0],
            "authors": authors,
            "text": clip(text, config.BRANCH_TEXT_MAX),
            "created_at": now_iso(),
        }
        self.branches[branch["branch_id"]] = branch
        self.emit("branch.created", {k: branch[k] for k in ("branch_id", "parent_ids", "op", "authors", "text")})
        return branch

    # -- withdrawing our own branches ---------------------------------------
    # Only an author hears every answer to its branch, so the author decides when nobody liked it.
    # The withdraw is a broadcast, so every character composts the same branches.

    def showed(self, branch_id: str, peer: str) -> None:
        """We showed a peer a branch. If it's ours, their answer counts toward whether anyone liked it."""
        if self.id in (self.branches.get(branch_id) or {}).get("authors", []):
            self.verdicts.setdefault(branch_id, {})[peer] = None

    def heard_verdict(self, branch_id: str | None, peer: str | None, liked: bool) -> None:
        if peer in self.verdicts.get(branch_id, {}):
            self.verdicts[branch_id][peer] = liked

    async def withdraw(self, branch_id: str, why: str) -> None:
        self.retired.add(branch_id)
        self.memory.add(f"You withdrew {branch_id}: {WITHDRAWN[why]}", "action", about=(branch_id,))
        self.emit(WITHDRAW, {"branch_id": branch_id, "why": why})
        await self.broadcast(WITHDRAW, {"branch_id": branch_id, "why": why})

    async def compost_unliked(self) -> None:
        """Withdraw our branches that everyone we showed them to turned down, once nobody living backs them."""
        if not config.on("compost"):
            return
        for branch_id, verdicts in list(self.verdicts.items()):
            waiting = any(liked is None and peer not in self.dead for peer, liked in verdicts.items())
            if waiting or any(verdicts.values()) or branch_id in self.retired or self.backers(branch_id):
                continue
            await self.withdraw(branch_id, "unliked")

    async def keep_budget(self) -> None:
        """Past BRANCH_BUDGET unbacked branches of our own, withdraw the oldest."""
        if not config.on("budget"):
            return
        mine = self.my_unbacked()
        for branch_id in mine[: max(0, len(mine) - config.BRANCH_BUDGET)]:
            await self.withdraw(branch_id, "budget")

    async def reply(self, env: dict, r: Reply) -> None:
        body = env.get("body") or {}
        sender = env.get("from")
        kind = env.get("type")
        branch_id = (body.get("branch") or {}).get("branch_id")
        idea = (r.idea or "").strip()
        if kind in (IDEA_SHARE, IDEA_PITCH):
            built = None
            if idea and branch_id in self.branches:
                # Their idea sparked one of ours: a twist of their branch, sent back with the answer.
                built = self.new_branch("twist", [branch_id], [self.id], idea)
                self.built_this_turn.append({**built, "_sent_to": (sender,)})
                self.social.tie(sender).you_built += 1
                self.memory.add(f"You built on {self.name_of(sender)}'s {branch_id}: {self.ref(built)}", "action", about=(sender, branch_id, built["branch_id"]))
                await self.keep_budget()
            yes = r.answer in ("accept", "counter")
            self.social.answer(sender, mine=False, yes=yes)
            verdict = f"{'accepted' if yes else 'rejected'} their {branch_id}"
            if built:
                verdict += f" and built on it as {self.ref(built)}"
            self.tie_note(sender, f"you {verdict}: {quote(r.text)}")
            if kind == IDEA_SHARE:
                await self.send(sender, IDEA_REACTION, {"branch_id": branch_id, "answer": "accept" if yes else "reject", "text": clip(r.text, 200), "branch": built})
            else:
                await self.send(sender, PITCH_RESPONSE, {"branch_id": branch_id, "persuaded": yes, "text": clip(r.text, 200), "branch": built})
        elif kind == MERGE_PROPOSE:
            branch = None
            if r.answer in ("accept", "counter"):
                text = idea if r.answer == "counter" and idea else body.get("text", "")
                parents = [b for b in body.get("branch_ids") or [] if b]
                branch = self.new_branch("merge", parents, [sender, self.id], text)
                self.memory.add(f"You and {self.name_of(sender)} merged {' and '.join(parents)} into {self.ref(branch)}", "action", about=(sender, branch["branch_id"]))
                await self.keep_budget()
            answer = r.answer
            self.social.answer(sender, mine=False, yes=branch is not None)
            self.tie_note(sender, f"you answered their merge offer: {answer}{', making ' + branch['branch_id'] if branch else ''}: {quote(r.text)}")
            await self.send(sender, MERGE_RESPONSE, {
                "proposal_id": body.get("proposal_id"), "answer": answer, "text": clip(r.text, 200), "branch": branch,
            })

    async def endorse(self, branch_id: str, reason: str) -> None:
        if branch_id in self.my_endorsed or branch_id not in self.branches:
            return
        if len(self.my_endorsed) >= config.MAX_ENDORSEMENTS:
            await self.unendorse(self.my_endorsed[0], "")  # making room isn't an objection
        self.my_endorsed.append(branch_id)
        self.endorsements[self.id] = list(self.my_endorsed)
        self.retired.discard(branch_id)  # backing a withdrawn branch brings it back
        self.support_changed()
        self.memory.add(f"You backed {self.ref(self.branches[branch_id])}", "action", about=(branch_id,))
        for author in self.branches[branch_id].get("authors") or []:
            if author != self.id:
                self.tie_note(author, f"you publicly backed their {branch_id}")
        self.emit(ENDORSE, {"branch_id": branch_id, "reason": clip(reason, 200)})
        await self.broadcast(ENDORSE, {"branch": self.branches[branch_id], "reason": clip(reason, 200)})

    async def unendorse(self, branch_id: str, reason: str) -> None:
        if branch_id not in self.my_endorsed:
            return
        self.my_endorsed.remove(branch_id)
        self.endorsements[self.id] = list(self.my_endorsed)
        self.support_changed()
        self.emit(UNENDORSE, {"branch_id": branch_id, "reason": reason})
        await self.broadcast(UNENDORSE, {"branch_id": branch_id, "reason": reason})

    async def trash(self, branch_id: str, reason: str) -> None:
        if branch_id in self.my_endorsed:
            await self.unendorse(branch_id, "")
        self.memory.add(f"You objected to {self.ref(self.branches[branch_id])}", "action", about=(branch_id,))
        for author in self.branches[branch_id].get("authors") or []:
            if author != self.id:
                self.tie_note(author, f"you publicly objected to their {branch_id}: {quote(reason)}")
        self.emit(OBJECTION, {"branch_id": branch_id, "reason": clip(reason, 200)})
        await self.broadcast(OBJECTION, {"branch_id": branch_id, "reason": clip(reason, 200)})

    async def reflect(self) -> None:
        peers = await self.living_peers()
        view = self.view(peers, [], False)
        recent = self.memory.since_reflection()
        self.memory.reflected()  # even if the call fails, so it doesn't retry every turn
        try:
            result = await self.think(
                "reflection", brain.reflection_prompt(view, recent), brain.reflection_model(),
                lambda: brain.mock_reflection(view, self.rng, recent),
            )
        except LLMError:
            return
        if config.on("reflect"):
            # An insight has to rest on memories it was shown, or it's dropped (the paper's citations).
            real = {m.id: m for m in self.memory.items}
            insights, dropped = [], 0
            for insight in result.value.insights[:3]:
                cited = [real[e] for e in (x.strip(" []") for x in insight.evidence) if e in real]
                if not cited or not insight.text.strip():
                    dropped += 1
                    continue
                insights.append(clip(insight.text, 300))
                about = set().union(*(m.about for m in cited))
                self.memory.add(insights[-1], "reflection", importance=8, about=about)
            living = {p["id"] for p in peers}
            for imp in result.value.impressions:
                if imp.peer_id in living and imp.impression.strip():
                    self.social.tie(imp.peer_id).impression = clip(imp.impression, 160)
                    self.memory.add(f"Your impression of {self.name_of(imp.peer_id)}: {clip(imp.impression, 160)}", "reflection", importance=7, about=(imp.peer_id,))
            if dropped:
                self.emit("reflection.dropped", {"insights": dropped, "why": "no valid evidence"})
        else:
            insights = [clip(i, 300) for i in result.value.insights[:3] if i.strip()]
            for insight in insights:
                self.memory.add(insight, "reflection", importance=8)
        self.agenda = clip(result.value.agenda, 300)
        impressions = [
            {"peer_id": i.peer_id, "impression": clip(i.impression, 160)} for i in getattr(result.value, "impressions", [])
        ]
        self.emit("agent.reflection", {"insights": insights, "agenda": self.agenda, "impressions": impressions})

    async def maybe_rest(self) -> None:
        """Broadcast rest once support has sat still long enough, and nothing is being designed."""
        if self.rested or self.seed is None or self.designs:
            return
        if not any(self.endorsements.values()):
            return  # nobody backs anything yet, so nothing has settled
        if time.monotonic() - self.last_support_change < config.scaled(config.REST_AFTER_S):
            return
        self.rested = True
        self.emit(REST, {})
        await self.broadcast(REST, {})

    # -- death ------------------------------------------------------------

    async def die(self) -> None:
        if not self.alive:
            return
        await self.finish_designs()  # others are counting on design work we were handed
        peers = await self.living_peers()
        view = self.view(peers, [], False)
        try:
            words = (await self.think(
                "last_words", brain.last_words_prompt(view), brain.LastWords, lambda: brain.mock_last_words(view, self.rng)
            )).value.text
        except LLMError:
            words = f"{self.name} is gone."
        self.alive = False
        words = clip(words, config.LAST_WORDS_MAX)
        self.emit(AGENT_LAST_WORDS, {"text": words})
        await self.broadcast(AGENT_LAST_WORDS, {"text": words})
        if config.on("compost"):
            # Nobody is left to champion them, unless a living co-author is.
            for branch_id in self.my_unbacked():
                if all(a == self.id or a in self.dead for a in self.branches[branch_id].get("authors") or []):
                    await self.withdraw(branch_id, "died")
        picked = await self.pick_designers(exclude={self.id}, include_me=False, peers=peers)
        if not picked:
            picked = [self.id]  # nobody is left to design a successor, so the town would end here
        generation = self.persona.get("lineage", {}).get("generation", 0) + 1
        await self.request_replacement(self.id, self.name, picked, generation)
        await self.finish_designs()
        self.emit("agent.died", {"picked": picked})
        self.done.set()

    async def finish_designs(self) -> None:
        while self.designs:
            await self.design(self.designs.popleft())

    async def pick_designers(self, exclude: set[str], include_me: bool, peers: list[dict] | None = None) -> list[str]:
        """Three random survivors. The first proposes, the next two refine, in that order."""
        peers = peers if peers is not None else await self.living_peers()
        candidates = [p["id"] for p in peers if p["id"] not in exclude and p["id"] not in self.dead]
        if include_me and self.alive:
            candidates.append(self.id)
        return self.rng.sample(candidates, min(3, len(candidates)))

    async def request_replacement(self, replaces: str, replaces_name: str, order: list[str], generation: int) -> None:
        briefing = self.briefing()
        for i, designer in enumerate(order):
            body = {
                "replaces": replaces, "replaces_name": replaces_name, "role": "propose" if i == 0 else "refine",
                "order": order, "generation": generation, "briefing": briefing,
            }
            if designer == self.id:
                self.designs.append({"type": REPLACE_REQUEST, "from": self.id, "body": body})
                self.wake.set()
            else:
                await self.send(designer, REPLACE_REQUEST, body)

    # -- designing a replacement ------------------------------------------

    async def design(self, env: dict) -> None:
        body = env.get("body") or {}
        kind = env.get("type")
        try:
            if kind == REPLACE_REQUEST and body.get("role") == "propose":
                await self.propose(body, next_step=1)
            elif kind == REPLACE_REQUEST:
                self.memory.add(f"You were picked to help design {body.get('replaces_name')}'s replacement", "replacement")
                self.spawn(self.design_watchdog(body))
            elif kind == PERSONA_DRAFT:
                await self.refine(body)
            elif kind == "takeover":
                await self.propose(body, next_step=body["order"].index(self.id) + 1)
        except LLMError:
            pass  # the next designer in line, or the watchdog, carries on

    async def design_town(self, exclude: str | None = None) -> list[dict]:
        peers = await self.living_peers()
        return [p for p in peers if p["id"] != exclude] + [self.persona]

    def newcomer_model(self) -> str:
        return config.model_override() or self.rng.choice(config.allowed_models())

    async def propose(self, body: dict, next_step: int) -> None:
        town = await self.design_town()
        taken = target = None
        if config.on("design"):
            taken = sorted({p.get("name") for p in [*self.known.values(), self.persona] if p.get("name")})
            target = replace.empty_corner(town, self.rng)
        if config.on("design"):
            result = await self.think(
                "propose",
                replace.propose_prompt(self.persona, town, body["replaces_name"], body.get("briefing") or {}, taken, target),
                replace.Proposal,
                lambda: replace.Proposal(concepts=["a", "b", "c"], names=[], draft=replace.mock_propose(town, self.rng)),
            )
            draft = result.value.draft
            body = {**body, "names": [n for n in result.value.names if n][:5]}
            draft.name = replace.free_name(draft.name, body["names"], taken)
        else:
            result = await self.think(
                "propose",
                replace.propose_prompt(self.persona, town, body["replaces_name"], body.get("briefing") or {}),
                replace.PersonaDraft,
                lambda: replace.mock_propose(town, self.rng),
            )
            draft = result.value
        persona = replace.to_persona(
            draft, model=self.newcomer_model(), replaces=body["replaces"], designers=body["order"], generation=body["generation"],
        )
        self.life_left -= 1
        self.drafts_seen.add(body["replaces"])
        self.memory.add(f"You proposed {persona['name']} ({persona['one_liner']}) to replace {body['replaces_name']}", "replacement")
        self.emit("replace.step", {"step": "propose", "replaces": body["replaces"], "persona": persona})
        await self.forward_draft(persona, body, next_step, [f"{self.name} proposed {persona['name']}"])

    async def refine(self, body: dict) -> None:
        draft = body["draft"]
        town = await self.design_town(exclude=draft.get("id"))
        feedback = None
        if config.on("design"):
            people = [p for p in [*self.known.values(), self.persona] if p.get("id") != draft.get("id")]
            feedback = replace.gaps_feedback(draft, people, sorted({p.get("name") for p in people if p.get("name")}))
        result = await self.think(
            "refine",
            replace.refine_prompt(self.persona, draft, town, body["replaces_name"], body.get("changes") or [], feedback),
            replace.Refined,
            lambda: replace.mock_refine(draft, town, self.rng),
        )
        persona = replace.to_persona(
            result.value.draft, model=draft.get("model") or self.newcomer_model(), replaces=body["replaces"],
            designers=body["order"], generation=body["generation"], persona_id=draft.get("id"),
        )
        self.life_left -= 1
        change = f"{self.name}: {clip(result.value.change, 150)}"
        self.memory.add(f"You refined {persona['name']}, the replacement for {body['replaces_name']}: {clip(result.value.change, 100)}", "replacement")
        self.emit("replace.step", {"step": "refine", "replaces": body["replaces"], "persona": persona, "change": change})
        await self.forward_draft(persona, body, body.get("step", 0) + 1, (body.get("changes") or []) + [change])

    async def forward_draft(self, persona: dict, body: dict, step: int, changes: list[str]) -> None:
        """Hand the draft to the next living designer, or birth it if nobody is left."""
        order = body["order"]
        for i in range(step, len(order)):
            if order[i] == self.id:
                continue
            payload = {
                "replaces": body["replaces"], "replaces_name": body["replaces_name"], "order": order, "step": i,
                "generation": body["generation"], "draft": persona, "changes": changes, "briefing": body.get("briefing") or {},
                "names": body.get("names") or [],
            }
            if await self.send(order[i], PERSONA_DRAFT, payload):
                return
        await self.birth(persona, body)

    async def design_watchdog(self, body: dict) -> None:
        """If every designer before us dies before a draft arrives, we take over the proposal."""
        await asyncio.sleep(config.scaled(config.DESIGN_TIMEOUT_S))
        if body["replaces"] in self.drafts_seen or not self.alive:
            return
        order = body["order"]
        mine = order.index(self.id) if self.id in order else -1
        living = {p["id"] for p in await self.living_peers()}
        if mine <= 0 or any(d in living for d in order[:mine]):
            return
        self.designs.append({"type": "takeover", "from": self.id, "body": body})
        self.wake.set()

    async def birth(self, persona: dict, body: dict) -> None:
        if config.on("design"):
            # Refiners may have renamed it into a clash. The proposer's other names are the way out.
            taken = sorted({p.get("name") for p in self.known.values() if p.get("name") and p.get("id") != persona.get("id")} | {self.name})
            persona = {**persona, "name": replace.free_name(persona["name"], body.get("names") or [], taken)}
        try:
            validate_persona(persona)
        except ValidationError as exc:
            log.warning("replacement for %s failed the schema check: %s", body["replaces"], exc)
            self.emit("replace.failed", {"replaces": body["replaces"], "error": str(exc)[:500]})
            return
        try:
            check = (await self.think(
                "content_check", replace.content_check_prompt(persona), replace.ContentCheck,
                lambda: replace.ContentCheck(ok=True, reason="mock check"),
            )).value
            ok, reason = check.ok, check.reason
        except LLMError:
            ok, reason = False, "the content check failed to run"
        world = await self.world_id()
        if not world:
            return
        if not ok:
            # A human approves or skips it. The answer comes back as a world.event human.answer.
            self.pending_reviews.append({"persona": persona, "replaces": body["replaces"]})
            await self.send(world, HUMAN_REQUEST, {"kind": "persona_review", "persona": persona, "flagged": clip(reason, 300)}, observe=False)
            return
        await self.spawn_newcomer(persona)

    async def spawn_newcomer(self, persona: dict) -> None:
        world = await self.world_id()
        if world and await self.send(world, SPAWN_REQUEST, {"persona": {**persona, "briefing": self.briefing()}}, observe=False):
            self.memory.add(f"You brought {persona['name']} to life", "replacement")
            self.emit("replace.step", {"step": "birth", "replaces": persona["lineage"]["replaces"], "persona": persona})

    def on_human_answer(self, text: str) -> None:
        """We never learn the request_id the world assigned to our request, so the oldest review takes the answer."""
        if not self.pending_reviews:
            return
        review = self.pending_reviews.pop(0)
        if text.strip().lower().startswith(("approve", "yes", "ok", "allow")):
            self.spawn(self.spawn_newcomer(review["persona"]))


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    agent_id = os.environ["AGENT_ID"]
    persona = json.loads(Path(os.environ["PERSONA_FILE"]).read_text())
    persona.setdefault("id", agent_id)
    if os.environ.get("ODDTOWN_MODE") == "ideation_loop":
        from agents.ideation import IdeationAgent

        await IdeationAgent(persona, os.environ.get("RUN_ID", "dev"), Town.from_env(agent_id)).run()
        return
    character = Character(persona, os.environ.get("RUN_ID", "dev"), Town.from_env(agent_id))
    await character.run()


if __name__ == "__main__":
    asyncio.run(main())
    os._exit(0)  # the HTTP and SLIM transports leave threads behind; exit 0 reads as a normal death
