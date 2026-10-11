"""What a character knows about each other character: the relationship ledger.

The memory stream holds everything, but a decision only sees the top few
memories, so who said what to whom falls out of view after a few turns. The
ledger keeps it per peer, written by code from what actually happened:

- every exchange between the two of you, newest last, in one short line each
- tallies: how often they said yes or no to your ideas and you to theirs, and
  who built on whose work
- their ideas, as branch ids
- your impression of them, in your own words, from your last reflection

Nothing here tells a character how to feel about anyone. It only keeps the
record straight, so what they say about each other comes from what happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field



@dataclass
class Tie:
    peer: str
    lines: list[tuple[int, str]] = field(default_factory=list)  # (step, what happened)
    they_yes: int = 0  # they accepted, were persuaded by, or built on your ideas
    they_no: int = 0
    you_yes: int = 0
    you_no: int = 0
    they_built: int = 0  # they built on your branches
    you_built: int = 0  # you built on theirs
    ideas: list[str] = field(default_factory=list)  # branch ids they wrote, oldest first
    impression: str = ""

    @property
    def talked(self) -> bool:
        return bool(self.lines)


class Ledger:
    def __init__(self):
        self.ties: dict[str, Tie] = {}

    def tie(self, peer: str) -> Tie:
        if peer not in self.ties:
            self.ties[peer] = Tie(peer)
        return self.ties[peer]

    def note(self, peer: str | None, step: int, line: str) -> None:
        if peer:
            self.tie(peer).lines.append((step, line))

    def their_idea(self, peer: str | None, branch_id: str | None) -> None:
        if peer and branch_id:
            ideas = self.tie(peer).ideas
            if branch_id not in ideas:
                ideas.append(branch_id)

    def answer(self, peer: str, *, mine: bool, yes: bool) -> None:
        """mine: the answer was to one of your ideas (they answered), else you answered theirs."""
        t = self.tie(peer)
        if mine:
            t.they_yes, t.they_no = t.they_yes + yes, t.they_no + (not yes)
        else:
            t.you_yes, t.you_no = t.you_yes + yes, t.you_no + (not yes)

    def summary(self, peer: str) -> str:
        t = self.ties.get(peer)
        if not t or not (t.talked or t.they_built or t.you_built):
            return "You haven't dealt with each other yet."
        parts = []
        if t.they_yes or t.they_no:
            parts.append(f"they took up {t.they_yes} of your ideas and turned down {t.they_no}")
        if t.you_yes or t.you_no:
            parts.append(f"you took up {t.you_yes} of theirs and turned down {t.you_no}")
        if t.they_built:
            parts.append(f"they built on your work {times(t.they_built)}")
        if t.you_built:
            parts.append(f"you built on theirs {times(t.you_built)}")
        return ("So far " + "; ".join(parts) + ".") if parts else "You've exchanged messages."

    def recent(self, peer: str, n: int) -> list[str]:
        t = self.ties.get(peer)
        return [line for _, line in t.lines[-n:]] if t else []

    def closeness(self, peer: str) -> int:
        """How much has passed between you, for choosing whose history to show in full."""
        t = self.ties.get(peer)
        return len(t.lines) + 2 * (t.they_built + t.you_built) if t else 0


def quote(text: str | None, limit: int = 120) -> str:
    text = clip(text, limit)
    return f'"{text}"' if text else "(no note)"


def clip(text: str | None, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def times(n: int) -> str:
    return {0: "never", 1: "once", 2: "twice"}.get(n, f"{n} times")
