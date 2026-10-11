"""Measures one run from observer.db: ideas, conversations and relationships.

    .venv/bin/python -m research.metrics RUN_ID [RUN_ID ...] [--db observer.db] [--json]

Everything here is computed from the stored events, so it costs no model calls
and a replayed run always scores the same.

Ideas
    branches        non-seed branches made
    cross_build     share of twists whose parent was written by someone else
    from_seed       share of twists made straight off the seed
    novelty         1 - highest TF-IDF cosine to any earlier branch, averaged
    near_dupes      share of branches with cosine >= 0.5 to an earlier one
    spread          mean pairwise distance between all branches at the end
    coauthored      branches with more than one author
    mixing          distinct characters in a branch's whole ancestry, mean and max
Conversation
    messages        shares, pitches and merge offers between characters
    accept_rate     share of replies that accept, are persuaded, or merge
    pairs           pairs of characters who exchanged at least one message
    reciprocal      pairs where both sides started an exchange
    threads         messages sent to someone who had already written to the sender
    volley          longest run of messages between one pair that alternates sides
    volleys_3plus   pairs who went back and forth at least three times in a row
    nagging         pitches or shares of a branch to someone who already turned it down
    bad_refs        branch ids in speech that never existed (a hallucination proxy)
Convergence
    endorsements    public endorsements made
    finalists       finalists at the end, with their living support
    ended_by        why the run ended (time budget, town at rest, ...)
Relationships
    allies, rivals  pairs whose accepts, merges and endorsements of each other's
                    work net to >= +2, or whose rejections and objections net to <= -2
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from itertools import combinations

STOP = set(
    "a an the and or but of to in on for with by at from as is are be was were it its this that these those "
    "into over under no not only just than then so we you they our your their them i my me he she his her "
    "every each all any some more most less can will would should could has have had do does did".split()
)
BRANCH_ID = re.compile(r"\bbr_[0-9a-f]{6}\b")


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", (text or "").lower()) if w not in STOP and len(w) > 2]


def tfidf(docs: list[list[str]]) -> list[dict[str, float]]:
    df = Counter(w for d in docs for w in set(d))
    n = len(docs)
    vecs = []
    for d in docs:
        tf = Counter(d)
        v = {w: (c / len(d)) * math.log((1 + n) / (1 + df[w])) + 1e-9 for w, c in tf.items()} if d else {}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs.append({w: x / norm for w, x in v.items()})
    return vecs


def cos(a: dict, b: dict) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


def load(db: sqlite3.Connection, run_id: str) -> list[tuple[int, str | None, str, dict]]:
    rows = db.execute("select seq, agent_id, type, payload from events where run_id=? order by seq", (run_id,))
    return [(s, a, t, json.loads(p)) for s, a, t, p in rows]


def measure(events: list[tuple[int, str | None, str, dict]]) -> dict:
    names: dict[str, str] = {}
    branches: dict[str, dict] = {}
    order: list[str] = []
    actions = Counter()
    msgs: list[tuple[str, str, str, str | None]] = []  # (from, to, type, branch_id)
    turned_down: set[tuple[str, str, str]] = set()  # (sender, recipient, branch) the recipient said no to
    nagging = 0
    started: set[tuple[str, str]] = set()
    wrote_to: set[tuple[str, str]] = set()
    threads = 0
    replies = accepts = 0
    pair = defaultdict(int)  # frozenset(a, b) -> net warmth
    speech = []
    talk = defaultdict(list)  # pair -> who spoke, in order
    seed = None
    born = 0
    endorsements = 0
    ending = {}

    def author_of(branch_id):
        b = branches.get(branch_id)
        return set(b.get("authors") or []) if b else set()

    def warm(a, b, delta):
        if a and b and a != b:
            pair[frozenset((a, b))] += delta

    for seq, agent, kind, p in events:
        if kind == "run.created":
            seed = p.get("seed")
        elif kind == "agent.born":
            born += 1
            names[agent] = p["persona"]["name"]
        elif kind == "branch.created":
            branches[p["branch_id"]] = p
            if p.get("op") != "seed":
                order.append(p["branch_id"])
        elif kind == "agent.decision":
            actions[p["action"]] += 1
            speech.append(p.get("say") or "")
            speech.append(p.get("reason") or "")
        elif kind in ("idea.share", "idea.pitch", "merge.propose"):
            to = p.get("to")
            branch_id = (p.get("branch") or {}).get("branch_id") or (p.get("branch_ids") or [None])[0]
            msgs.append((agent, to, kind, branch_id))
            talk[frozenset((agent, to))].append(agent)
            speech.append(p.get("note") or "")
            if (agent, to, branch_id) in turned_down:
                nagging += 1
            if (to, agent) in wrote_to:
                threads += 1
            wrote_to.add((agent, to))
            started.add((agent, to))
        elif kind in ("idea.reaction", "pitch.response", "merge.response"):
            replies += 1
            to = p.get("to")
            talk[frozenset((agent, to))].append(agent)
            yes = p.get("answer") in ("accept", "counter") or p.get("persuaded") is True
            accepts += yes
            warm(agent, to, 1 if yes else -1)
            wrote_to.add((agent, to))
            speech.append(p.get("text") or "")
            if not yes and p.get("branch_id"):
                turned_down.add((to, agent, p["branch_id"]))
        elif kind == "run.ended":
            ending = p
        elif kind == "endorse":
            endorsements += 1
            for a in author_of(p.get("branch_id")):
                warm(agent, a, 1)
        elif kind == "objection":
            for a in author_of(p.get("branch_id")):
                warm(agent, a, -1)

    def volley(speakers):
        best = run = 1 if speakers else 0
        for prev, cur in zip(speakers, speakers[1:]):
            run = run + 1 if cur != prev else 1
            best = max(best, run)
        return best

    volleys = {pr: volley(sp) for pr, sp in talk.items() if len(pr) == 2}

    def ancestry(branch_id, seen=None):
        seen = seen if seen is not None else set()
        b = branches.get(branch_id)
        if not b or branch_id in seen:
            return set()
        seen.add(branch_id)
        people = {a for a in b.get("authors") or [] if a != "world"}
        for parent in b.get("parent_ids") or []:
            people |= ancestry(parent, seen)
        return people

    mixing = [len(ancestry(b)) for b in order]
    twists = [branches[b] for b in order if branches[b]["op"] == "twist"]
    cross = [t for t in twists if (author_of((t.get("parent_ids") or [None])[0]) - set(t["authors"])) and t["parent_ids"][0] != "br_seed"]
    from_seed = [t for t in twists if (t.get("parent_ids") or [None])[0] == "br_seed"]

    texts = [words(branches[b]["text"]) for b in order]
    vecs = tfidf(texts + ([words(seed)] if seed else []))[: len(texts)]
    novelty, dupes = [], 0
    for i in range(1, len(vecs)):
        best = max(cos(vecs[i], vecs[j]) for j in range(i))
        novelty.append(1 - best)
        dupes += best >= 0.5
    spread = [1 - cos(a, b) for a, b in combinations(vecs, 2)]

    known = set(branches)
    bad_refs = sum(1 for s in speech for ref in BRANCH_ID.findall(s) if ref not in known)
    pairs = {frozenset((a, b)) for a, b, *_ in msgs if a and b}
    reciprocal = {pr for pr in pairs if len(pr) == 2 and all((x, y) in started for x, y in [tuple(pr), tuple(pr)[::-1]])}

    def n(pr):
        return " & ".join(sorted(names.get(x, x) for x in pr))

    return {
        "seed": seed,
        "characters": born,
        "decisions": sum(actions.values()),
        "actions": dict(actions.most_common()),
        "branches": len(order),
        "cross_build": round(len(cross) / len(twists), 2) if twists else 0,
        "from_seed": round(len(from_seed) / len(twists), 2) if twists else 0,
        "merges": sum(1 for b in order if branches[b]["op"] == "merge"),
        "coauthored": sum(1 for b in order if len(set(branches[b]["authors"])) > 1),
        "mixing": f"{round(sum(mixing) / len(mixing), 2) if mixing else 0} (max {max(mixing, default=0)})",
        "novelty": round(sum(novelty) / len(novelty), 3) if novelty else 0,
        "near_dupes": round(dupes / max(1, len(novelty)), 2),
        "spread": round(sum(spread) / len(spread), 3) if spread else 0,
        "messages": len(msgs),
        "replies": replies,
        "accept_rate": round(accepts / replies, 2) if replies else 0,
        "pairs": len(pairs),
        "reciprocal": len(reciprocal),
        "threads": round(threads / len(msgs), 2) if msgs else 0,
        "volley": max(volleys.values(), default=0),
        "volleys_3plus": sum(1 for v in volleys.values() if v >= 3),
        "nagging": nagging,
        "bad_refs": bad_refs,
        "endorsements": endorsements,
        "finalists": [f.get("support") for f in ending.get("finalists", [])],
        "ended_by": ending.get("reason"),
        "allies": sorted(n(p) for p, v in pair.items() if v >= 2),
        "rivals": sorted(n(p) for p, v in pair.items() if v <= -2),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--db", default="observer.db")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    db = sqlite3.connect(args.db)
    results = {run: measure(load(db, run)) for run in args.runs}
    if args.json:
        print(json.dumps(results, indent=2))
        return
    for run, m in results.items():
        print(f"== {run}")
        for k, v in m.items():
            print(f"  {k:12} {v}")


if __name__ == "__main__":
    main()
