"""The ideation voting loop: a second run mode, next to the town.

Round 1. Every agent writes one idea from the seed and broadcasts it. Every
other agent twists it, so A agents make A originals plus A*(A-1) twists, A*A
ideas in all. Each idea is linked to its last editor (the author of an
original, the twister of a twist). Every agent then casts exactly one vote,
over every idea except the A it's linked to.

Rounds 2 to R. The top N ideas from the round before go back out. Every agent
twists all N, making A*N new ideas that replace the old ones, and votes over
the N*(A-1) it isn't linked to.

Picking the top N: whole vote levels are taken while they fit, and the rest
of the slots are drawn at random from the level that doesn't fit. With N=6
and votes 6, 6, 5, 3, 3, 1, 1, ..., that's the top five plus one random 1.
Ideas with no votes are a level too, so a round with fewer than N voted
ideas fills up from them.

Between rounds (after rounds 1 to R-1, never mid-round), one agent dies and is
replaced, so the agent count stays A. If an agent crashed during the round,
that crash is the round's death. Otherwise the world draws one agent at random
(seeded, like the fill) and tells it to retire. Either way the replacement is
designed by three random survivors with the town's mechanics (agents/design.py),
and the next round waits until the newcomer has joined. If the design fails
twice or times out, the world starts an unused persona from the cast instead.

The world service only runs the clock and counts. Ideas, twists and votes
come from the agents, which broadcast them, so the world hears them like
every agent does. Each phase has a deadline: a missing twist is skipped and a
missing or invalid vote is an abstention.

Messages. The world broadcasts:
    ideation.originate  {"round", "seed", "constraints"}
    ideation.twist      {"round", "ideas": [idea, ...]}            twist every one not linked to you
    ideation.vote       {"round", "ideas": [idea, ...]}            vote for one not linked to you
    ideation.result     {"round", "selected": [idea], "tally", "random_fill"}
and sends one agent:
    ideation.retire          {"round"}                             say last words, pick designers, leave
    ideation.replace_crashed {"replaces", "replaces_name", "generation"}   pick designers for a crashed agent
Agents broadcast:
    ideation.idea       {"idea": {"idea_id", "text", "author", "parent_id", "round"}}
    ideation.ballot     {"round", "idea_id", "reason"}
and the last designer, if a replacement can't pass its checks:
    ideation.replace_failed  {"replaces", "reason"}
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

log = logging.getLogger("world.ideation")

MODE = "ideation_loop"
SEED_IDEA_ID = "br_seed"


class LoopSettings(BaseModel):
    agents: int = Field(6, ge=3, le=12)
    rounds: int = Field(3, ge=3, le=10)
    top_n: int = Field(3, ge=3, le=6)
    # How long a phase waits for slow agents. LLM twists of up to 11 ideas take a while.
    phase_timeout_s: float = Field(180, gt=0)
    # Seeds the random fill and who retires, so a run can be replayed. Defaults to one drawn per run.
    random_seed: int | None = None
    # One agent dies and is replaced between rounds. Off keeps the same cast all run.
    replace_each_round: bool = True
    # How long the next round waits for a designed newcomer before falling back to an unused persona.
    replace_timeout_s: float = Field(240, gt=0)


def select_top(pool: list[str], votes: dict[str, int], n: int, rng: random.Random) -> tuple[list[str], list[str]]:
    """The top n ideas of `pool` by votes, and which of them were drawn at random.

    Whole vote levels go in while they fit. The first level that doesn't fit
    fills the remaining slots by a random draw. `pool` order doesn't matter.
    """
    levels: dict[int, list[str]] = {}
    for idea_id in sorted(pool):
        levels.setdefault(votes.get(idea_id, 0), []).append(idea_id)
    selected: list[str] = []
    random_fill: list[str] = []
    for count in sorted(levels, reverse=True):
        level = levels[count]
        room = n - len(selected)
        if room <= 0:
            break
        if len(level) <= room:
            selected.extend(level)
        else:
            drawn = rng.sample(level, room)
            selected.extend(drawn)
            random_fill.extend(drawn)
    return selected, random_fill


def eligible(pool: list[dict], voter: str) -> list[dict]:
    """The ideas a voter may vote for: every one it isn't the last editor of."""
    return [idea for idea in pool if idea["author"] != voter]


@dataclass
class RoundState:
    number: int
    phase: str = "originate"  # originate, twist, vote, done
    originals: list[dict] = field(default_factory=list)
    to_twist: list[dict] = field(default_factory=list)
    twists: list[dict] = field(default_factory=list)
    pool: list[dict] = field(default_factory=list)
    ballots: dict[str, str] = field(default_factory=dict)  # voter -> idea_id
    reasons: dict[str, str] = field(default_factory=dict)


class IdeationLoop:
    """Runs the rounds for one world-service run."""

    def __init__(self, world, run, settings: LoopSettings, participants: list[str], seed_text: str, rng_seed: int,
                 spare_personas: list[dict] | None = None):
        self.world = world
        self.run = run
        self.settings = settings
        self.participants = list(participants)
        self.seed_text = seed_text
        self.rng = random.Random(rng_seed)
        self.round: RoundState | None = None
        self.history: list[dict] = []  # one summary per finished round
        self.selected: list[dict] = []  # the latest top N
        self.replacements: list[dict] = []  # one per round boundary
        self.spare_personas = list(spare_personas or [])  # the fallback when a design fails
        self._replace_failed: set[str] = set()
        self._filled: set[str] = set()  # replacements already done, so a late design can't add an agent
        self._launched: dict[str, str] = {}  # victim -> the designed newcomer's id, once the world started it
        self._changed = asyncio.Condition()

    # -- what the agents send ------------------------------------------------

    async def observe(self, envelope: dict) -> None:
        kind = envelope.get("type")
        sender = envelope.get("from")
        body = envelope.get("body") or {}
        if kind == "ideation.replace_failed":
            # Arrives between rounds, while no round is going, so it's checked first.
            self._replace_failed.add(str(body.get("replaces")))
            async with self._changed:
                self._changed.notify_all()
            return
        r = self.round
        if r is None:
            return
        if sender not in self.participants:
            return
        if kind == "ideation.idea":
            idea = body.get("idea") or {}
            if idea.get("round") != r.number or idea.get("author") != sender or not idea.get("idea_id"):
                return
            idea = {k: idea.get(k) for k in ("idea_id", "text", "author", "parent_id", "round")}
            if not isinstance(idea["text"], str) or not idea["text"].strip():
                return
            if r.phase == "originate" and idea["parent_id"] == SEED_IDEA_ID:
                if not any(i["author"] == sender for i in r.originals):
                    r.originals.append(idea)
            elif r.phase == "twist" and self._valid_twist(r, idea):
                r.twists.append(idea)
        elif kind == "ideation.ballot":
            if r.phase != "vote" or body.get("round") != r.number or sender in r.ballots:
                return
            choice = body.get("idea_id")
            if choice in {i["idea_id"] for i in eligible(r.pool, sender)}:
                r.ballots[sender] = choice
                r.reasons[sender] = str(body.get("reason") or "")[:300]
        else:
            return
        async with self._changed:
            self._changed.notify_all()

    def _valid_twist(self, r: RoundState, idea: dict) -> bool:
        parent = next((i for i in r.to_twist if i["idea_id"] == idea["parent_id"]), None)
        if parent is None or (r.number == 1 and parent["author"] == idea["author"]):
            return False  # in round 1 nobody twists their own original
        return not any(t["author"] == idea["author"] and t["parent_id"] == idea["parent_id"] for t in r.twists)

    async def _wait_until(self, done, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        async with self._changed:
            while not done():
                left = deadline - time.monotonic()
                if left <= 0 or self.run.state != "running":
                    return False
                try:
                    await asyncio.wait_for(self._changed.wait(), timeout=min(left, 1.0))
                except asyncio.TimeoutError:
                    pass
        return True

    # -- the rounds ----------------------------------------------------------

    async def run_all(self) -> None:
        timeout = self.settings.phase_timeout_s
        for number in range(1, self.settings.rounds + 1):
            if self.run.state != "running":
                return
            r = RoundState(number)
            self.round = r
            await self.world.emit("loop.round_started", {
                "round": number,
                "ideas": self.selected,
                "participants": self.participants,
            })

            if number == 1:
                await self._broadcast("ideation.originate", {
                    "round": 1, "seed": self.seed_text, "constraints": list(self.run.constraints),
                })
                await self._wait_until(lambda: len(r.originals) >= len(self.participants), timeout)
                r.to_twist = list(r.originals)
            else:
                r.to_twist = list(self.selected)

            r.phase = "twist"
            expected = self._expected_twists(r)
            await self._broadcast("ideation.twist", {"round": number, "ideas": r.to_twist, **self._context()})
            await self._wait_until(lambda: len(r.twists) >= expected, timeout)

            # Round 1 votes over originals and twists. Later rounds only over the new twists.
            r.pool = (r.originals if number == 1 else []) + r.twists
            r.phase = "vote"
            await self._broadcast("ideation.vote", {"round": number, "ideas": r.pool, **self._context()})
            voters = [p for p in self.participants if eligible(r.pool, p)]
            await self._wait_until(lambda: all(v in r.ballots for v in voters), timeout)

            r.phase = "done"
            votes: dict[str, int] = {}
            for choice in r.ballots.values():
                votes[choice] = votes.get(choice, 0) + 1
            ids, random_fill = select_top([i["idea_id"] for i in r.pool], votes, self.settings.top_n, self.rng)
            by_id = {i["idea_id"]: i for i in r.pool}
            self.selected = [{**by_id[i], "votes": votes.get(i, 0)} for i in ids]
            summary = {
                "round": number,
                "pool_size": len(r.pool),
                "originals": len(r.originals) if number == 1 else 0,
                "twists": len(r.twists),
                "expected_twists": expected,
                "ballots": dict(r.ballots),
                "reasons": dict(r.reasons),
                "abstained": sorted(set(self.participants) - set(r.ballots)),
                "tally": votes,
                "selected": self.selected,
                "random_fill": random_fill,
            }
            self.history.append(summary)
            await self.world.emit("loop.round_ended", summary)
            await self._broadcast("ideation.result", {
                "round": number, "selected": self.selected, "tally": votes, "random_fill": random_fill,
            })
            if number < self.settings.rounds and self.settings.replace_each_round:
                self.round = None
                await self.replace_one(number)
        self.round = None

    def _context(self) -> dict:
        """The seed and constraints ride along, so a newcomer never works blind."""
        return {"seed": self.seed_text, "constraints": list(self.run.constraints)}

    # -- one death and one birth between rounds --------------------------------

    async def replace_one(self, after_round: int) -> None:
        launcher = self.run.launcher
        running = launcher.running() if launcher else set(self.participants)
        crashed = [p for p in self.participants if p not in running]
        records = {r["id"]: r for r in await self.world.town.characters()}
        if crashed:
            victim, how = crashed[0], "crash"
            # Nobody else would notice it now, since no round is going. The world asks a random survivor to.
            await self.world.town.registry.remove(victim)
            survivors = [p for p in self.participants if p in running]
            if survivors:
                noticer = self.rng.choice(survivors)
                persona = (records.get(victim) or {}).get("persona") or {}
                generation = (persona.get("lineage") or {}).get("generation", 0) + 1
                await self._send(noticer, "ideation.replace_crashed", {
                    "replaces": victim, "replaces_name": persona.get("name") or victim, "generation": generation,
                })
        else:
            victim, how = self.rng.choice(sorted(self.participants)), "random"
            await self._send(victim, "ideation.retire", {"round": after_round})
        await self.world.emit("loop.retiring", {"round": after_round, "agent_id": victim, "how": how}, victim)

        # The victim leaves on its own after its last words. If it hangs, it's killed.
        if how == "random":
            await self._wait_until(lambda: victim not in (launcher.running() if launcher else set()), 60)
            if launcher and victim in launcher.running():
                launcher.kill(victim)

        newcomer = await self._wait_for_newcomer(victim)
        fallback = None
        self._filled.add(victim)  # from here on, a late design for this victim is refused
        if newcomer is None and victim in self._launched:
            # Designed and already starting up, just slow to join. Wait for it rather than adding a spare.
            newcomer = await self._wait_for_newcomer(victim, timeout=self.run.request.ready_timeout_s)
        if newcomer is None:
            fallback = "design failed" if victim in self._replace_failed else "design timed out"
            newcomer = await self._start_spare(victim, after_round)
        if newcomer:
            self.participants[self.participants.index(victim)] = newcomer
        else:
            self.participants.remove(victim)  # nobody to replace it with, the loop goes on one short
        record = {"round": after_round, "died": victim, "how": how, "born": newcomer, "fallback": fallback}
        self.replacements.append(record)
        await self.world.emit("loop.replaced", record)

    def accepts_spawn(self, persona: dict) -> bool:
        """A spawn for a replacement that's already filled would add an extra agent, so it's refused."""
        replaces = ((persona or {}).get("lineage") or {}).get("replaces")
        if replaces in self._filled:
            return False
        if replaces:
            self._launched[replaces] = persona.get("id")
        return True

    async def _wait_for_newcomer(self, victim: str, timeout: float | None = None) -> str | None:
        """The id of the character that registered to replace `victim`, once it has joined."""
        found: list[str] = []

        async def check() -> bool:
            for r in await self.world.town.characters():
                lineage = ((r.get("persona") or {}).get("lineage") or {})
                if lineage.get("replaces") == victim and r["id"] not in self.participants:
                    found.append(r["id"])
                    return True
            return victim in self._replace_failed

        deadline = time.monotonic() + (timeout if timeout is not None else self.settings.replace_timeout_s)
        while time.monotonic() < deadline and self.run.state == "running":
            if await check():
                break
            await asyncio.sleep(0.25)
        return found[0] if found else None

    async def _start_spare(self, victim: str, after_round: int) -> str | None:
        launched = set(self.run.launcher.procs) if self.run.launcher else set()
        spares = [p for p in self.spare_personas if p["id"] not in launched]
        if not spares or not self.run.launcher:
            log.warning("no unused persona left to replace %s", victim)
            return None
        spare = self.rng.choice(spares)
        persona = {
            **spare,
            "lineage": {"generation": 1, "designed_by": [], "replaces": victim},
            "briefing": {"seed": self.seed_text, "constraints": list(self.run.constraints)},
        }
        await self.run.launcher.start(persona)
        await self.world.announce_birth(self.run, persona, "world")
        return persona["id"] if persona["id"] in await self.world.living() else None

    async def _send(self, to: str, type: str, body: dict) -> None:
        try:
            await self.world.town.send(to, {"type": type, "body": body})
        except Exception as exc:  # a dead target shows up as a missing newcomer, and the fallback covers it
            log.warning("could not send %s to %s: %s", type, to, exc)

    def _expected_twists(self, r: RoundState) -> int:
        if r.number == 1:
            return sum(1 for p in self.participants for i in r.to_twist if i["author"] != p)
        return len(self.participants) * len(r.to_twist)

    async def _broadcast(self, type: str, body: dict) -> None:
        await self.world.town.broadcast({"type": type, "body": body})

    def finalists(self) -> list[dict]:
        """The latest top N, shaped for the observer's run.ended."""
        return [
            {
                "branch_id": idea["idea_id"],
                "text": idea["text"],
                "champions": [idea["author"]],
                "minority_report": "",
                "votes": idea.get("votes", 0),
                "round": idea.get("round"),
            }
            for idea in self.selected
        ]
