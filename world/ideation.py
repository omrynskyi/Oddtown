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

The world service only runs the clock and counts. Ideas, twists and votes
come from the agents, which broadcast them, so the world hears them like
every agent does. Each phase has a deadline: a missing twist is skipped and a
missing or invalid vote is an abstention.

Messages. The world broadcasts:
    ideation.originate  {"round", "seed", "constraints"}
    ideation.twist      {"round", "ideas": [idea, ...]}            twist every one not linked to you
    ideation.vote       {"round", "ideas": [idea, ...]}            vote for one not linked to you
    ideation.result     {"round", "selected": [idea], "tally", "random_fill"}
Agents broadcast:
    ideation.idea       {"idea": {"idea_id", "text", "author", "parent_id", "round"}}
    ideation.ballot     {"round", "idea_id", "reason"}
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
    # Seeds the random fill, so a run can be replayed. Defaults to one drawn per run.
    random_seed: int | None = None


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

    def __init__(self, world, run, settings: LoopSettings, participants: list[str], seed_text: str, rng_seed: int):
        self.world = world
        self.run = run
        self.settings = settings
        self.participants = list(participants)
        self.seed_text = seed_text
        self.rng = random.Random(rng_seed)
        self.round: RoundState | None = None
        self.history: list[dict] = []  # one summary per finished round
        self.selected: list[dict] = []  # the latest top N
        self._changed = asyncio.Condition()

    # -- what the agents send ------------------------------------------------

    async def observe(self, envelope: dict) -> None:
        r = self.round
        if r is None:
            return
        kind = envelope.get("type")
        sender = envelope.get("from")
        body = envelope.get("body") or {}
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
            await self._broadcast("ideation.twist", {"round": number, "ideas": r.to_twist})
            await self._wait_until(lambda: len(r.twists) >= expected, timeout)

            # Round 1 votes over originals and twists. Later rounds only over the new twists.
            r.pool = (r.originals if number == 1 else []) + r.twists
            r.phase = "vote"
            await self._broadcast("ideation.vote", {"round": number, "ideas": r.pool})
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
        self.round = None

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
