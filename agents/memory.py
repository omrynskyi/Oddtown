"""A character's memory stream, retrieval and reflection trigger.

Every observation lands here with its text, kind and importance. Kinds that
carry ideas or news get their importance rated by the character on its next
decision call. Mechanical kinds (endorsements seen, rests, hellos, the
character's own acts) get a fixed score, so the decision call never spends
tokens rating them.

Retrieval is the Generative Agents score: recency (0.99 per step), importance
and relevance, each normalized to 0..1, top 8. Relevance needs no embeddings.
Every observation records who and which branches it is `about`, and a turn has
a focus (the peers and branches in its inbox, its agenda). Relevance is how
much an observation's `about` overlaps the focus, plus a little word overlap
with the focus text. With no focus, retrieval is recency plus importance.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from agents import config

# Kinds the character rates itself, through its own worldview.
SCORED_KINDS = {"heard_idea", "reaction", "pitch", "merge", "world", "death", "objection", "human"}

# Fixed importance for everything else.
FIXED_IMPORTANCE = {"endorsement": 3, "rest": 2, "hello": 5, "action": 5, "replacement": 6, "reflection": 8}


@dataclass
class Observation:
    id: str
    seq: int
    text: str
    kind: str
    step: int
    ts: float = field(default_factory=time.time)
    importance: int = config.DEFAULT_IMPORTANCE
    scored: bool = False
    about: frozenset[str] = frozenset()  # agent ids and branch ids it mentions


class MemoryStream:
    def __init__(self):
        self.items: list[Observation] = []
        self.step = 0  # one per decision turn, drives recency
        self._next = 0
        self._importance_since_reflection = 0

    def add(self, text: str, kind: str, importance: int | None = None, about=()) -> Observation:
        self._next += 1
        if importance is None and kind not in SCORED_KINDS:
            importance = FIXED_IMPORTANCE.get(kind, config.DEFAULT_IMPORTANCE)
        obs = Observation(
            id=f"obs_{self._next}",
            seq=self._next,
            text=text,
            kind=kind,
            step=self.step,
            importance=importance if importance is not None else config.DEFAULT_IMPORTANCE,
            scored=importance is not None,
            about=frozenset(a for a in about if a),
        )
        self.items.append(obs)
        if obs.scored and kind != "reflection":
            self._importance_since_reflection += obs.importance
        return obs

    def unscored(self, limit: int = config.MAX_SCORED_PER_CALL) -> list[Observation]:
        """Observations waiting for the character to rate them, newest last."""
        pending = [o for o in self.items if not o.scored]
        return pending[-limit:]

    def score(self, obs_id: str, importance: int) -> None:
        for obs in reversed(self.items):
            if obs.id == obs_id and not obs.scored:
                obs.importance = max(1, min(10, int(importance)))
                obs.scored = True
                self._importance_since_reflection += obs.importance
                return

    def settle_through(self, seq: int) -> None:
        """After a call, anything up to `seq` it didn't rate keeps the default score for good.

        Observations that arrived while the call was running have a higher seq and
        wait for the next call.
        """
        for obs in self.items:
            if not obs.scored and obs.seq <= seq:
                obs.scored = True
                self._importance_since_reflection += obs.importance

    def retrieve(
        self,
        k: int = config.RETRIEVE_TOP_K,
        exclude: set[str] | None = None,
        focus: set[str] | None = None,
        query: str = "",
    ) -> list[Observation]:
        candidates = [o for o in self.items if not exclude or o.id not in exclude]
        if not candidates:
            return []
        recency = [config.RECENCY_DECAY ** (self.step - o.step) for o in candidates]
        importance = [float(o.importance) for o in candidates]
        r_norm, i_norm = _normalize(recency), _normalize(importance)
        if focus or query:
            q = _words(query)
            relevance = [len(o.about & focus) + _overlap(_words(o.text), q) if focus else _overlap(_words(o.text), q) for o in candidates]
            v_norm = _normalize(relevance) if max(relevance) > 0 else [0.0] * len(candidates)
        else:
            v_norm = [0.0] * len(candidates)
        ranked = sorted(
            range(len(candidates)),
            key=lambda i: (r_norm[i] + i_norm[i] + v_norm[i], candidates[i].step, candidates[i].id),
            reverse=True,
        )
        return [candidates[i] for i in ranked[:k]]

    def wants_reflection(self) -> bool:
        return self._importance_since_reflection >= config.REFLECT_AT_IMPORTANCE

    def since_reflection(self, limit: int = 15) -> list[Observation]:
        """The most important recent memories, for the reflection call."""
        recent = [o for o in self.items if o.kind != "reflection"][-40:]
        return sorted(recent, key=lambda o: (o.importance, o.step), reverse=True)[:limit]

    def reflected(self) -> None:
        self._importance_since_reflection = 0


def _normalize(values: list[float]) -> list[float]:
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [1.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


_STOP = set("the and for with that this you your they them their from into have has was were are not but its about".split())


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower()) if w not in _STOP}


def _overlap(a: set[str], b: set[str]) -> float:
    """Share of the query's words the memory mentions, 0..1."""
    return len(a & b) / len(b) if a and b else 0.0
