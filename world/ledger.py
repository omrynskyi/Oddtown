"""Everything public the world service hears, and the finalists it adds up.

Endorsements, rests, births and deaths are broadcast, so the world service
sees them like any character does. Idea content still only travels by gossip;
the ledger only learns a branch's text when an endorsement carries it.

Nothing here decides anything. It counts living support and reports it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_ENDORSEMENTS = 2


@dataclass
class Objection:
    agent_id: str
    branch_id: str
    reason: str
    seq: int


@dataclass
class Ledger:
    # agent -> branch ids it backs, oldest first
    endorsements: dict[str, list[str]] = field(default_factory=dict)
    # (agent, branch) -> the reason it gave
    reasons: dict[tuple[str, str], str] = field(default_factory=dict)
    branches: dict[str, dict] = field(default_factory=dict)
    objections: list[Objection] = field(default_factory=list)
    resting: set[str] = field(default_factory=set)
    dead: set[str] = field(default_factory=set)
    _seq: int = 0

    def observe(self, envelope: dict) -> None:
        self._seq += 1
        kind = envelope.get("type")
        sender = envelope.get("from")
        body = envelope.get("body") or {}
        if not sender:
            return
        if kind == "endorse":
            self._endorse(sender, body)
        elif kind == "unendorse":
            self._unendorse(sender, body)
        elif kind == "objection":
            branch_id = body.get("branch_id")
            if branch_id:
                self.objections.append(Objection(sender, branch_id, body.get("reason", ""), self._seq))
        elif kind == "rest":
            self.resting.add(sender)
        elif kind == "agent.last_words":
            self.mark_dead(sender)

    def _branch_id(self, body: dict) -> str | None:
        branch = body.get("branch")
        if isinstance(branch, dict) and branch.get("branch_id"):
            # Branches never change once made, so the first copy we hear is the branch.
            self.branches.setdefault(branch["branch_id"], dict(branch))
            return branch["branch_id"]
        return body.get("branch_id")

    def _endorse(self, sender: str, body: dict) -> None:
        branch_id = self._branch_id(body)
        if not branch_id:
            return
        backed = self.endorsements.setdefault(sender, [])
        if branch_id in backed:
            backed.remove(branch_id)
        backed.append(branch_id)
        while len(backed) > MAX_ENDORSEMENTS:  # agents enforce this too, the ledger just never trusts it
            backed.pop(0)
        if body.get("reason"):
            self.reasons[(sender, branch_id)] = body["reason"]
        self.resting.clear()  # support moved

    def _unendorse(self, sender: str, body: dict) -> None:
        branch_id = self._branch_id(body)
        if not branch_id:
            return
        backed = self.endorsements.get(sender, [])
        if branch_id in backed:
            backed.remove(branch_id)
            self.resting.clear()
        if body.get("reason"):
            self.objections.append(Objection(sender, branch_id, body["reason"], self._seq))

    def mark_dead(self, agent_id: str) -> None:
        self.dead.add(agent_id)
        self.resting.discard(agent_id)

    def support(self, living: set[str]) -> dict[str, list[str]]:
        """branch id -> living backers."""
        backers: dict[str, list[str]] = {}
        for agent, branch_ids in self.endorsements.items():
            if agent in living and agent not in self.dead:
                for branch_id in branch_ids:
                    backers.setdefault(branch_id, []).append(agent)
        return backers

    def majority_resting(self, living: set[str]) -> bool:
        alive = living - self.dead
        return bool(alive) and len(self.resting & alive) * 2 > len(alive)

    def finalists(self, living: set[str], k: int = 3) -> dict:
        """Top k branches by living support, each with its backers and a minority report."""
        support = self.support(living)
        ranked = sorted(support.items(), key=lambda item: (-len(item[1]), item[0]))
        top = ranked[:k]
        finalists = []
        for branch_id, backers in top:
            finalists.append(
                {
                    "branch_id": branch_id,
                    "branch": self.branches.get(branch_id),
                    "support": len(backers),
                    "champions": [
                        {"agent_id": a, "reason": self.reasons.get((a, branch_id))} for a in sorted(backers)
                    ],
                    "minority_report": self._minority_report(branch_id, backers, support),
                }
            )
        ties = []
        if len(ranked) > k and top and len(ranked[k][1]) == len(top[-1][1]):
            tied = [b for b, backers in ranked if len(backers) == len(top[-1][1])]
            ties.append(tied)
        return {"finalists": finalists, "ties": ties}

    def _minority_report(self, branch_id: str, backers: list[str], support: dict[str, list[str]]) -> dict | None:
        """The objection to this branch from the strongest rival faction, newest first on ties."""
        rivals = [o for o in self.objections if o.branch_id == branch_id and o.agent_id not in backers]
        if not rivals:
            return None

        def faction_strength(objection: Objection) -> int:
            own = self.endorsements.get(objection.agent_id, [])
            return max((len(support.get(b, [])) for b in own), default=0)

        best = max(rivals, key=lambda o: (faction_strength(o), o.seq))
        return {"agent_id": best.agent_id, "reason": best.reason}
