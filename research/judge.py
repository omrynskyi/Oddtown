"""LLM-judged measures of a run, for what counting can't see.

    .venv/bin/python -m research.judge RUN_ID [--db observer.db] [--sample 40]

grounding   For messages that mention another character, the judge sees the full
            record of what actually passed between the two before that moment and
            lists the claims the message makes about the other character or their
            history, each marked supported or not. Reports the share of
            unsupported claims, a hallucination rate.
ideas       Every branch is rated 1 to 5 for originality (how far from the
            obvious take on the seed) and coherence (can you picture how it
            works). Reports the means and how many branches score 4+ on both.

The judge is the town's own model (ODDTOWN_MODEL), so its biases are the same
for every run it scores. Results are cached next to the db, keyed by run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sqlite3
from pathlib import Path

from pydantic import BaseModel, Field

from agents import config, llm
from agents.llm import Section
from research.metrics import load

MESSAGES = ("idea.share", "idea.pitch", "merge.propose", "idea.reaction", "pitch.response", "merge.response")


class Claim(BaseModel):
    claim: str
    supported: bool = Field(description="True only if the record shows it")


class Grounding(BaseModel):
    claims: list[Claim] = Field(description="Claims about the other character or your past with them. Empty if none")


class Rating(BaseModel):
    branch_id: str
    originality: int = Field(description="1 obvious take on the seed, 5 a genuinely new mechanism")
    coherence: int = Field(description="1 can't picture how it works, 5 clear how it would work")


class Ratings(BaseModel):
    ratings: list[Rating]


def text_of(kind: str, p: dict) -> str:
    return p.get("note") or p.get("text") or ""


def record_between(events, a: str, b: str, before: int, names: dict) -> list[str]:
    lines = []
    for seq, agent, kind, p in events:
        if seq >= before:
            break
        if kind in MESSAGES and {agent, p.get("to")} == {a, b}:
            verdict = p.get("answer") or ("persuaded" if p.get("persuaded") else ("not persuaded" if "persuaded" in p else ""))
            branch = (p.get("branch") or {}).get("branch_id") or p.get("branch_id") or ""
            lines.append(f"[{seq}] {names.get(agent, agent)} -> {names.get(p['to'], p['to'])}: {kind} {branch} {verdict} \"{text_of(kind, p)}\"")
        elif kind in ("endorse", "objection") and agent in (a, b):
            lines.append(f"[{seq}] {names.get(agent, agent)} publicly {kind}d {p.get('branch_id')}: \"{p.get('reason') or ''}\"")
        elif kind == "branch.created" and agent in (a, b):
            lines.append(f"[{seq}] {names.get(agent, agent)} wrote {p['branch_id']} ({p['op']} of {p.get('parent_ids')}): \"{p['text']}\"")
    return lines


async def grounding(events, sample: int) -> dict:
    names = {a: p["persona"]["name"] for _, a, k, p in events if k == "agent.born"}
    first = {a: n.split()[0].lower() for a, n in names.items()}
    candidates = []
    for seq, agent, kind, p in events:
        if kind not in MESSAGES or not p.get("to"):
            continue
        text = text_of(kind, p)
        if len(text) > 20:
            candidates.append((seq, agent, p["to"], text))
    random.Random(0).shuffle(candidates)
    rows = []
    for seq, a, b, text in candidates[:sample]:
        record = record_between(events, a, b, seq, names) or ["(nothing yet)"]
        system = [Section("rules", (
            "You check messages between characters in a simulation for false claims. You get the complete record of "
            "everything that passed between two characters, and one message. List every claim the message makes about "
            "the other character or about their past dealings (what they said, proposed, rejected, backed, believe). "
            "Ideas, opinions about ideas and proposals are not claims. Mark a claim supported only if the record shows it."
        ))]
        user = [
            Section("record", f"Record between {names.get(a, a)} and {names.get(b, b)}, oldest first:\n" + "\n".join(record[-30:])),
            Section("message", f"Message from {names.get(a, a)} to {names.get(b, b)}: \"{text}\""),
        ]
        try:
            result = await llm.call_json(kind="judge", model=config.model_override(), system=system, user=user, out=Grounding, mock=None)
        except llm.LLMError:
            continue
        rows.append({"seq": seq, "from": names.get(a, a), "to": names.get(b, b), "text": text, "claims": [c.model_dump() for c in result.value.claims]})
    claims = [c for r in rows for c in r["claims"]]
    bad = [c for c in claims if not c["supported"]]
    return {
        "messages": len(rows),
        "with_claims": sum(1 for r in rows if r["claims"]),
        "claims": len(claims),
        "unsupported": len(bad),
        "unsupported_rate": round(len(bad) / len(claims), 2) if claims else 0,
        "examples": [{"from": r["from"], "to": r["to"], "claim": c["claim"]} for r in rows for c in r["claims"] if not c["supported"]][:6],
        "rows": rows,
    }


async def ideas(events) -> dict:
    seed = next((p["seed"] for _, _, k, p in events if k == "run.created"), "")
    branches = [(p["branch_id"], p["text"]) for _, _, k, p in events if k == "branch.created" and p["op"] != "seed"]
    ratings: dict[str, Rating] = {}
    for i in range(0, len(branches), 10):
        chunk = branches[i : i + 10]
        system = [Section("rules", (
            "You rate product ideas that riff on a seed idea. Originality: 1 is the obvious take anyone would suggest, "
            "5 is a genuinely new mechanism nobody would expect. Coherence: 1 you can't picture how it would work, "
            "5 it's clear how it works. Rate each idea on its own, harshly and consistently."
        ))]
        user = [Section("ideas", f"Seed: {seed}\n" + "\n".join(f"{b}: {t}" for b, t in chunk))]
        try:
            result = await llm.call_json(kind="judge", model=config.model_override(), system=system, user=user, out=Ratings, mock=None)
        except llm.LLMError:
            continue
        for r in result.value.ratings:
            ratings[r.branch_id] = r
    rated = [ratings[b] for b, _ in branches if b in ratings]
    o = [max(1, min(5, r.originality)) for r in rated]
    c = [max(1, min(5, r.coherence)) for r in rated]
    return {
        "rated": len(rated),
        "originality": round(sum(o) / len(o), 2) if o else 0,
        "coherence": round(sum(c) / len(c), 2) if c else 0,
        "strong": sum(1 for r in rated if r.originality >= 4 and r.coherence >= 4),
        "strong_share": round(sum(1 for r in rated if r.originality >= 4 and r.coherence >= 4) / len(rated), 2) if rated else 0,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--db", default="observer.db")
    parser.add_argument("--sample", type=int, default=40)
    parser.add_argument("--cache", default="research/judged.json")
    args = parser.parse_args()
    cache_file = Path(args.cache)
    cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    db = sqlite3.connect(args.db)
    for run in args.runs:
        if run not in cache:
            events = load(db, run)
            cache[run] = {"grounding": await grounding(events, args.sample), "ideas": await ideas(events)}
            cache_file.write_text(json.dumps(cache, indent=1))
        g, i = cache[run]["grounding"], cache[run]["ideas"]
        print(f"== {run}")
        print(f"  grounding  {g['claims']} claims in {g['messages']} messages, {g['unsupported']} unsupported ({g['unsupported_rate']})")
        for e in g["examples"][:3]:
            print(f"     ✗ {e['from']} -> {e['to']}: {e['claim']}")
        print(f"  ideas      originality {i['originality']}, coherence {i['coherence']}, strong {i['strong']}/{i['rated']} ({i['strong_share']})")


if __name__ == "__main__":
    asyncio.run(main())
