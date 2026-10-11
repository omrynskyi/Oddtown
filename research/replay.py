"""Replays logged decision prompts under prompt variants, holding the context fixed.

    ODDTOWN_PROMPT_LOG=... (set during a run) records every prompt. This takes the
    decision prompts that had messages waiting, swaps the rules and the output
    schema, and asks the model again, so a change in what comes back is down to
    the variant and not to a different situation.

    .venv/bin/python -m research.replay PROMPT_DIR --n 30 --out replay.json

Variants:
    v1          the original rules and field order (a resample of the logged call)
    v1_think    original rules, reasoning written before the action
    v2          rewritten rules with build replies, reasoning last
    v2_think    rewritten rules with build replies, reasoning first (the new default)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
from collections import Counter
from pathlib import Path

from agents import brain, llm
from agents.llm import Section

VARIANTS = {
    "v1": "think,build,rules",
    "v1_think": "build,rules",
    "v2": "think",
    "v2_think": "",
}


def load(folder: str) -> list[dict]:
    records = []
    for f in sorted(Path(folder).glob("*.jsonl")):
        records += [json.loads(line) for line in f.read_text().splitlines()]
    return [r for r in records if r["kind"] == "decision" and "msg " in r["user"].get("inbox", "")]


async def ask(record: dict, variant: str, sem: asyncio.Semaphore) -> dict:
    os.environ["ODDTOWN_ABLATE"] = VARIANTS[variant]
    rules = brain.RULES if "rules" not in VARIANTS[variant] else brain.RULES_V1
    model = brain.decision_model()
    system = [Section("rules", rules), Section("persona", record["system"]["persona"])]
    user = [Section(k, v) for k, v in record["user"].items() if k != "format"]
    async with sem:
        try:
            result = await llm.call_json(kind="decision", model=record["model"], system=system, user=user, out=model, mock=None)
        except llm.LLMError as exc:
            return {"variant": variant, "agent": record["agent"], "error": str(exc)}
    return {"variant": variant, "agent": record["agent"], "inbox": record["user"]["inbox"], "answer": result.value.model_dump()}


def summarize(results: list[dict]) -> dict:
    out = {}
    for variant in VARIANTS:
        rows = [r for r in results if r["variant"] == variant and "answer" in r]
        actions = Counter(r["answer"]["action"] for r in rows)
        answers = Counter(rep["answer"] for r in rows for rep in r["answer"]["replies"])
        twists = [r["answer"] for r in rows if r["answer"]["action"] == "twist"]
        off_seed = sum(1 for t in twists if t.get("target_branch") not in (None, "br_seed"))
        out[variant] = {
            "n": len(rows),
            "errors": sum(1 for r in results if r["variant"] == variant and "error" in r),
            "actions": dict(actions.most_common()),
            "replies": dict(answers.most_common()),
            "yes_rate": round((answers["accept"] + answers["build"] + answers["counter"]) / max(1, sum(answers.values())), 2),
            "twist_off_seed": f"{off_seed}/{len(twists)}",
            "reason_len": round(sum(len(r["answer"]["reason"]) for r in rows) / max(1, len(rows))),
        }
    return out


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder")
    parser.add_argument("--n", type=int, default=30)
    parser.add_argument("--out", default="replay.json")
    parser.add_argument("--variants", default=",".join(VARIANTS))
    args = parser.parse_args()
    records = load(args.folder)
    random.Random(0).shuffle(records)
    records = records[: args.n]
    sem = asyncio.Semaphore(1)  # one at a time, so ODDTOWN_ABLATE is set for the call that reads it
    results = []
    for record in records:
        for variant in args.variants.split(","):
            results.append(await ask(record, variant, sem))
    Path(args.out).write_text(json.dumps(results, indent=1))
    print(json.dumps(summarize(results), indent=1))


if __name__ == "__main__":
    asyncio.run(main())
