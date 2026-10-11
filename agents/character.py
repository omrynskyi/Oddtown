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
import time
import uuid
from collections import deque
from pathlib import Path

from pydantic import ValidationError

from agents import brain, config, llm, replace
from agents.brain import Decision, Reply, View, clip
from agents.llm import LLMError
from agents.memory import MemoryStream
from agents.messages import (
    AGENT_HELLO, AGENT_LAST_WORDS, ENDORSE, HUMAN_REQUEST, IDEA_PITCH, IDEA_REACTION, IDEA_SHARE,
    MERGE_PROPOSE, MERGE_RESPONSE, OBJECTION, PERSONA_DRAFT, PITCH_RESPONSE, REPLACE_REQUEST, REST,
    SPAWN_REQUEST, SPAWN_RESULT, UNENDORSE, USAGE_REPORT, WORLD_EVENT, envelope,
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
        self.seed: str | None = None
        self.constraints: list[str] = []
        self.branches: dict[str, dict] = {}
        self.endorsements: dict[str, list[str]] = {}  # public: agent id -> branch ids it backs
        self.my_endorsed: list[str] = []
        self.agenda = ""
        self.known: dict[str, dict] = {}  # last seen persona for every peer, kept after they die
        self.dead: set[str] = set()

        self.inbox: list[dict] = []  # waking messages the next decision call answers
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
            if kind == IDEA_SHARE:
                self.memory.add(f'{who} shared {self.ref(branch)} with you. Note: "{clip(body.get("note"), 80)}"', "heard_idea")
            elif kind == IDEA_PITCH:
                self.memory.add(f'{who} pitched {self.ref(branch)} to you: "{clip(body.get("note"), 80)}"', "pitch")
            else:
                a, b = (body.get("branch_ids") or ["?", "?"])[:2]
                self.memory.add(f'{who} offered to merge {a} and {b} into: "{clip(body.get("text"), 80)}"', "merge")
            self.inbox.append(env)
            self.wake.set()
        elif kind in (REPLACE_REQUEST, PERSONA_DRAFT):
            self.designs.append(env)
            if kind == PERSONA_DRAFT:
                self.drafts_seen.add(body.get("replaces"))
            self.wake.set()
        elif kind == IDEA_REACTION:
            self.memory.add(f'{who} reacted to your {body.get("branch_id")}: {body.get("answer")}, "{clip(body.get("text"), 100)}"', "reaction")
        elif kind == PITCH_RESPONSE:
            verdict = "persuaded" if body.get("persuaded") else "not persuaded"
            self.memory.add(f'{who} was {verdict} by your pitch for {body.get("branch_id")}: "{clip(body.get("text"), 100)}"', "reaction")
        elif kind == MERGE_RESPONSE:
            branch = self.learn(body.get("branch"))
            answer = body.get("answer")
            result = f", making {branch['branch_id']}" if branch else ""
            self.memory.add(f'{who} answered your merge offer: {answer}{result}. "{clip(body.get("text"), 100)}"', "merge")
        elif kind == ENDORSE:
            branch = self.learn(body.get("branch"))
            branch_id = branch["branch_id"] if branch else body.get("branch_id")
            if branch_id:
                self.public_endorse(sender, branch_id)
                self.memory.add(f"{who} now backs {self.ref(self.branches.get(branch_id)) or branch_id}", "endorsement")
        elif kind == UNENDORSE:
            branch_id = body.get("branch_id")
            if branch_id:
                self.public_unendorse(sender, branch_id)
                reason = f': "{clip(body.get("reason"), 80)}"' if body.get("reason") else ""
                self.memory.add(f"{who} dropped {branch_id}{reason}", "endorsement")
        elif kind == OBJECTION:
            self.memory.add(f'{who} objected to {body.get("branch_id")}: "{clip(body.get("reason"), 100)}"', "objection")
        elif kind == AGENT_HELLO:
            replaces = body.get("replaces")
            note = f", replacing {self.name_of(replaces)}" if replaces else ""
            self.memory.add(f"{body.get('name') or who} joined the town{note}: {clip(body.get('one_liner'), 100)}", "hello")
        elif kind == AGENT_LAST_WORDS:
            self.mark_dead(sender)
            self.memory.add(f'{who} died. Last words: "{clip(body.get("text"), 150)}"', "death")
        elif kind == "human.request.result":
            # The world tells us which request_id its answer will carry.
            for review in self.pending_reviews:
                if review.get("ref") and review["ref"] == body.get("ref"):
                    review["request_id"] = body.get("request_id")
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
            self.on_human_answer(text, body.get("request_id"))
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
        return self.branches[branch["branch_id"]]

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
        self.support_changed()

    def public_unendorse(self, agent_id: str, branch_id: str) -> None:
        backed = self.endorsements.get(agent_id, [])
        if branch_id in backed:
            backed.remove(branch_id)
            self.support_changed()

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
        top = sorted(self.branches.values(), key=lambda b: (b.get("op") != "seed", -support.get(b["branch_id"], 0)))[:8]
        return {
            "seed": self.seed,
            "constraints": self.constraints,
            "branches": top,
            "endorsements": {a: ids for a, ids in self.endorsements.items() if a in living and ids},
        }

    def view(self, peers: list[dict], inbox: list[dict], idle: bool) -> View:
        to_score = self.memory.unscored()
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
            memories=self.memory.retrieve(exclude={o.id for o in to_score}),
            inbox=inbox,
            to_score=to_score,
            idle=idle,
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
        inbox, self.inbox = self.inbox, []
        self.news = False
        peers = await self.living_peers()
        view = self.view(peers, inbox, idle)
        counts = {
            "memories": len(view.memories), "new_obs": len(view.to_score), "waking": len(inbox),
            "branches": len(brain.pick_branches(view)), "idle": idle,
        }
        try:
            result = await self.think(
                "decision", brain.decision_prompt(view), Decision, lambda: brain.mock_decision(view, self.rng), counts
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
        for reply in d.replies:
            if reply.msg_id in by_msg:
                await self.reply(by_msg.pop(reply.msg_id), reply)

        if action == "twist":
            branch = self.new_branch("twist", [target], [self.id], d.text)
            self.memory.add(f"You twisted {target} into {self.ref(branch)}", "action")
            if send_to:
                await self.send(send_to, IDEA_SHARE, {"branch": branch, "note": clip(d.note, 200)})
        elif action == "merge":
            proposal = {"proposal_id": f"mp_{uuid.uuid4().hex[:8]}", "branch_ids": [target, other], "text": clip(d.text, config.BRANCH_TEXT_MAX), "note": clip(d.note, 200)}
            self.memory.add(f"You offered {self.name_of(send_to)} a merge of {target} and {other}", "action")
            await self.send(send_to, MERGE_PROPOSE, proposal)
        elif action == "back":
            await self.endorse(target, d.reason)
        elif action == "trash":
            await self.trash(target, d.reason)
        elif action == "pitch":
            self.memory.add(f"You pitched {target} to {self.name_of(send_to)}", "action")
            await self.send(send_to, IDEA_PITCH, {"branch": self.branches[target], "note": clip(d.note, 200)})

        for branch_id in d.unendorse:
            if branch_id in self.my_endorsed:
                await self.unendorse(branch_id, "")
        for branch_id in d.endorse:
            if branch_id in self.branches:
                await self.endorse(branch_id, d.reason)

        if d.flag_human and d.flag_human.branch_id in self.branches:
            world = await self.world_id()
            if world:
                flag = d.flag_human
                body = {"kind": flag.kind, "branch_id": flag.branch_id, "branch": self.branches[flag.branch_id], "claim": clip(flag.claim, 300)}
                await self.send(world, HUMAN_REQUEST, body, observe=False)
                self.memory.add(f"You asked a human to rule on {flag.branch_id}: {clip(flag.claim, 100)}", "action")

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

    async def reply(self, env: dict, r: Reply) -> None:
        body = env.get("body") or {}
        sender = env.get("from")
        kind = env.get("type")
        branch_id = (body.get("branch") or {}).get("branch_id")
        if kind == IDEA_SHARE:
            answer = "accept" if r.answer == "accept" else "reject"
            await self.send(sender, IDEA_REACTION, {"branch_id": branch_id, "answer": answer, "text": clip(r.text, 200)})
        elif kind == IDEA_PITCH:
            await self.send(sender, PITCH_RESPONSE, {"branch_id": branch_id, "persuaded": r.answer == "accept", "text": clip(r.text, 200)})
        elif kind == MERGE_PROPOSE:
            branch = None
            if r.answer in ("accept", "counter"):
                text = r.text if r.answer == "counter" and (r.text or "").strip() else body.get("text", "")
                parents = [b for b in body.get("branch_ids") or [] if b]
                branch = self.new_branch("merge", parents, [sender, self.id], text)
                self.memory.add(f"You and {self.name_of(sender)} merged {' and '.join(parents)} into {self.ref(branch)}", "action")
            await self.send(sender, MERGE_RESPONSE, {
                "proposal_id": body.get("proposal_id"), "answer": r.answer, "text": clip(r.text, 200), "branch": branch,
            })

    async def endorse(self, branch_id: str, reason: str) -> None:
        if branch_id in self.my_endorsed or branch_id not in self.branches:
            return
        if len(self.my_endorsed) >= config.MAX_ENDORSEMENTS:
            await self.unendorse(self.my_endorsed[0], "")  # making room isn't an objection
        self.my_endorsed.append(branch_id)
        self.endorsements[self.id] = list(self.my_endorsed)
        self.support_changed()
        self.memory.add(f"You backed {self.ref(self.branches[branch_id])}", "action")
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
        self.memory.add(f"You objected to {self.ref(self.branches[branch_id])}", "action")
        self.emit(OBJECTION, {"branch_id": branch_id, "reason": clip(reason, 200)})
        await self.broadcast(OBJECTION, {"branch_id": branch_id, "reason": clip(reason, 200)})

    async def reflect(self) -> None:
        peers = await self.living_peers()
        view = self.view(peers, [], False)
        recent = self.memory.since_reflection()
        self.memory.reflected()  # even if the call fails, so it doesn't retry every turn
        try:
            result = await self.think(
                "reflection", brain.reflection_prompt(view, recent), brain.Reflection, lambda: brain.mock_reflection(view, self.rng)
            )
        except LLMError:
            return
        insights = [clip(i, 300) for i in result.value.insights[:3] if i.strip()]
        for insight in insights:
            self.memory.add(insight, "reflection", importance=8)
        self.agenda = clip(result.value.agenda, 300)
        self.emit("agent.reflection", {"insights": insights, "agenda": self.agenda})

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
        picked = await self.pick_designers(exclude={self.id}, include_me=False, peers=peers)
        generation = self.persona.get("lineage", {}).get("generation", 0) + 1
        await self.request_replacement(self.id, self.name, picked, generation)
        self.emit("agent.died", {"picked": picked})
        self.done.set()

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
        result = await self.think(
            "propose",
            replace.propose_prompt(self.persona, town, body["replaces_name"], body.get("briefing") or {}),
            replace.PersonaDraft,
            lambda: replace.mock_propose(town, self.rng),
        )
        persona = replace.to_persona(
            result.value, model=self.newcomer_model(), replaces=body["replaces"], designers=body["order"], generation=body["generation"],
        )
        self.life_left -= 1
        self.drafts_seen.add(body["replaces"])
        self.memory.add(f"You proposed {persona['name']} ({persona['one_liner']}) to replace {body['replaces_name']}", "replacement")
        self.emit("replace.step", {"step": "propose", "replaces": body["replaces"], "persona": persona})
        await self.forward_draft(persona, body, next_step, [f"{self.name} proposed {persona['name']}"])

    async def refine(self, body: dict) -> None:
        draft = body["draft"]
        town = await self.design_town(exclude=draft.get("id"))
        result = await self.think(
            "refine",
            replace.refine_prompt(self.persona, draft, town, body["replaces_name"], body.get("changes") or []),
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
            ref = uuid.uuid4().hex
            self.pending_reviews.append({"persona": persona, "replaces": body["replaces"], "ref": ref, "request_id": None})
            await self.send(world, HUMAN_REQUEST, {"kind": "persona_review", "ref": ref, "persona": persona, "flagged": clip(reason, 300)}, observe=False)
            return
        await self.spawn_newcomer(persona)

    async def spawn_newcomer(self, persona: dict) -> None:
        world = await self.world_id()
        if world and await self.send(world, SPAWN_REQUEST, {"persona": {**persona, "briefing": self.briefing()}}, observe=False):
            self.memory.add(f"You brought {persona['name']} to life", "replacement")
            self.emit("replace.step", {"step": "birth", "replaces": persona["lineage"]["replaces"], "persona": persona})

    def on_human_answer(self, text: str, request_id: str | None = None) -> None:
        """Only the review the answer was for takes it. The world told us its request_id after we asked."""
        review = next((r for r in self.pending_reviews if request_id and r.get("request_id") == request_id), None)
        if review is None:
            return  # an answer to someone else's question
        self.pending_reviews.remove(review)
        if text.strip().lower().startswith(("approve", "yes", "ok", "allow")):
            self.spawn(self.spawn_newcomer(review["persona"]))


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    agent_id = os.environ["AGENT_ID"]
    persona = json.loads(Path(os.environ["PERSONA_FILE"]).read_text())
    persona.setdefault("id", agent_id)
    character = Character(persona, os.environ.get("RUN_ID", "dev"), Town.from_env(agent_id))
    await character.run()


if __name__ == "__main__":
    asyncio.run(main())
    os._exit(0)  # the HTTP and SLIM transports leave threads behind; exit 0 reads as a normal death
