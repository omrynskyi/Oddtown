"""The end-of-run summary: one LLM call over a whole run, stored as a run.summary event.

    payload = await summarize(store.events(run_id))

It reads the run's events, so it works the same for a run that just ended and an
old one replayed from the database. The call returns a headline, the top three
verdicts, every branch sorted into themed buckets, and the key moments. If the
world service left run.ended's baseline empty, a second call fills it with one
LLM's three ideas, so the UI can put the town next to a single model.

Set OBSERVER_SUMMARY_MODEL to pick the model. ODDTOWN_MODEL still overrides it,
so ODDTOWN_MODEL=mock summarizes with no API key.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from pydantic import BaseModel, Field

from agents.baseline import run_baseline
from agents.llm import Section, call_json

log = logging.getLogger("observer.summary")

MAX_BRANCHES = 60
CLIP = 240
EMOJIS = ["🌱", "🤝", "🛠️", "💡", "🏘️", "🚀"]


class Verdict(BaseModel):
    branch_id: str = Field(description="The id of the branch, exactly as listed")
    title: str = Field(description="A short catchy name for it, six words or fewer")
    why: str = Field(description="Why it wins, two sentences or fewer")
    risk: str = Field(description="Its biggest risk, one sentence")


class Bucket(BaseModel):
    name: str = Field(description="A fun name for the theme, four words or fewer")
    emoji: str = Field(description="One emoji for the theme")
    blurb: str = Field(description="One short line on what the ideas in it share")
    branch_ids: list[str] = Field(description="The ids of the branches in it")


class Summary(BaseModel):
    headline: str = Field(description="The town's verdict in one sentence, 20 words or fewer")
    verdicts: list[Verdict] = Field(description="The three strongest versions of the idea, best first")
    buckets: list[Bucket] = Field(description="Three to six themes that every branch but the seed falls into")
    moments: list[str] = Field(description="Three to five key moments of the run, one sentence each")


def clip(text: str | None, limit: int = CLIP) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def digest(events: list[dict[str, Any]]) -> dict[str, Any]:
    """What happened in a run, pulled out of its events."""
    names: dict[str, str] = {}
    run: dict[str, Any] = {
        "seed": "", "constraints": [], "branches": {}, "backing": {}, "reasons": {},
        "objections": [], "deaths": [], "finalists": [], "baseline": [],
    }
    for e in events:
        p, agent = e.get("payload") or {}, e.get("agent_id")
        kind = e["type"]
        if kind == "run.created":
            run["seed"] = p.get("seed", "")
        elif kind == "agent.born":
            names[p["persona"]["id"]] = p["persona"]["name"]
        elif kind == "world.event" and p.get("kind") == "constraint":
            run["constraints"].append(p.get("text", ""))
        elif kind == "branch.created":
            run["branches"][p["branch_id"]] = p
        elif kind in ("endorse", "unendorse") and agent:
            backed = [b for b in run["backing"].get(agent, []) if b != p.get("branch_id")]
            if kind == "endorse":
                backed.append(p.get("branch_id"))
                if p.get("reason"):
                    run["reasons"][(agent, p.get("branch_id"))] = p["reason"]
            run["backing"][agent] = backed
        elif kind == "objection" and agent:
            run["objections"].append((agent, p.get("branch_id"), p.get("reason", "")))
        elif kind == "agent.died" and agent and agent not in run["deaths"]:
            run["deaths"].append(agent)
        elif kind == "run.ended":
            run["finalists"] = p.get("finalists", [])
            run["baseline"] = p.get("baseline", [])
    run["names"] = names
    support: dict[str, list[str]] = {}
    for agent, branch_ids in run["backing"].items():
        if agent not in run["deaths"]:
            for b in branch_ids:
                support.setdefault(b, []).append(agent)
    run["support"] = support
    return run


def prompt(run: dict[str, Any]) -> list[Section]:
    name = lambda id: run["names"].get(id, id)
    branches = list(run["branches"].values())[-MAX_BRANCHES:]
    lines = []
    for b in branches:
        backers = run["support"].get(b["branch_id"], [])
        origin = "the seed" if b["op"] == "seed" else f"{b['op']} by {', '.join(map(name, b['authors']))} from {', '.join(b['parent_ids'])}"
        lines.append(f"{b['branch_id']} [{origin}; {len(backers)} backers: {', '.join(map(name, backers)) or 'none'}] {clip(b['text'])}")
    sections = [
        Section("seed", f"Seed idea: {run['seed']}"),
        Section("constraints", "Constraints added during the run: " + (" | ".join(run["constraints"]) or "none")),
        Section("branches", "Every version of the idea:\n" + "\n".join(lines)),
    ]
    reasons = [f"{name(a)} backs {b}: {clip(r, 160)}" for (a, b), r in run["reasons"].items()]
    if reasons:
        sections.append(Section("reasons", "Why characters backed what they backed:\n" + "\n".join(reasons[-30:])))
    if run["objections"]:
        objections = [f"{name(a)} objects to {b}: {clip(r, 160)}" for a, b, r in run["objections"][-20:]]
        sections.append(Section("objections", "Objections:\n" + "\n".join(objections)))
    if run["deaths"]:
        sections.append(Section("deaths", "Characters who died during the run: " + ", ".join(map(name, run["deaths"]))))
    if run["finalists"]:
        finalists = [f"{f['branch_id']} ({len(f.get('champions', []))} champions)" for f in run["finalists"]]
        sections.append(Section("finalists", "The town's finalists by living support: " + ", ".join(finalists)))
    sections.append(Section("task", (
        "Pick the three strongest versions as verdicts, best first. Weigh the town's support but use your own judgment. "
        "Sort every branch except the seed into three to six themed buckets, and give each a fun name. "
        "Write a one-sentence headline with the town's verdict, and three to five key moments of the run. "
        "Be concrete and lively, name the characters, and use only branch ids from the list above."
    )))
    return sections


RULES = (
    "You are the chronicler of Oddtown, where a handful of characters with clashing worldviews spent a run "
    "twisting, merging, backing and objecting to versions of one idea. Summarize the run for the person who "
    "asked, so they come away with useful takeaways, not just a recap."
)


def mock_summary(run: dict[str, Any]) -> Summary:
    """A summary with no LLM, built from support counts, so a mock run still fills the results screen."""
    seed = run["seed"]
    name = lambda id: run["names"].get(id, id)
    rest = lambda text: clip(text[len(seed):] if text.startswith(seed) else text, 60).strip(" ,.") or text
    branches = [b for b in run["branches"].values() if b["op"] != "seed"]
    ranked = sorted(branches, key=lambda b: (-len(run["support"].get(b["branch_id"], [])), b["branch_id"]))
    report = {f["branch_id"]: f.get("minority_report", "") for f in run["finalists"]}
    verdicts = [
        Verdict(
            branch_id=b["branch_id"],
            title=" ".join(rest(b["text"]).split()[:6]).capitalize(),
            why=f"{plural(len(run['support'].get(b['branch_id'], [])), 'living character')} back it at the end of the run.",
            risk=report.get(b["branch_id"]) or "Nobody has tested it outside the town yet.",
        )
        for b in ranked[:3]
    ]
    # One bucket per twist straight off the seed, holding everything that grew from it.
    root: dict[str, str] = {}
    family: dict[str, list[str]] = {}
    for b in branches:
        parent = next((p for p in b["parent_ids"] if p in root), None)
        root[b["branch_id"]] = root[parent] if parent else b["branch_id"]
        family.setdefault(root[b["branch_id"]], []).append(b["branch_id"])
    groups = sorted(family.items(), key=lambda item: -len(item[1]))
    buckets = [
        Bucket(name=" ".join(rest(run["branches"][root]["text"]).split()[:3]).title(), emoji=EMOJIS[i % len(EMOJIS)],
               blurb=f"{plural(len(ids), 'version')} that grew from {name(run['branches'][root]['authors'][0])}'s twist.", branch_ids=ids)
        for i, (root, ids) in enumerate(groups[:5])
    ]
    if len(groups) > 5:
        buckets.append(Bucket(name="Odds And Ends", emoji="🎲", blurb="Everything else the town tried.",
                              branch_ids=[b for _, ids in groups[5:] for b in ids]))
    moments = [f"The town wrote {len(branches)} versions of “{seed}”."]
    moments += [f"A new rule landed: {c}" for c in run["constraints"][:1]]
    moments += [f"{name(d)} left town for good." for d in run["deaths"][:2]]
    if verdicts:
        moments.append(f"“{verdicts[0].title}” finished on top.")
    top = verdicts[0].title if verdicts else seed
    return Summary(headline=f"The town rallied behind “{top}”.", verdicts=verdicts, buckets=buckets, moments=moments)


def tidy(summary: Summary, run: dict[str, Any]) -> dict[str, Any]:
    """Drop ids the model made up, and branches it put in more than one bucket."""
    known = set(run["branches"])
    verdicts, seen = [], set()
    for v in summary.verdicts:
        if v.branch_id in known and v.branch_id not in seen:
            seen.add(v.branch_id)
            verdicts.append(v.model_dump())
    buckets, placed = [], set()
    for b in summary.buckets:
        ids = [i for i in dict.fromkeys(b.branch_ids) if i in known and i not in placed]
        placed.update(ids)
        if ids:
            buckets.append({**b.model_dump(), "branch_ids": ids})
    return {
        "headline": clip(summary.headline, 200),
        "verdicts": verdicts[:3],
        "buckets": buckets[:6],
        "moments": [clip(m, 200) for m in summary.moments if m.strip()][:5],
    }


async def summarize(events: list[dict[str, Any]], model: str | None = None) -> dict[str, Any]:
    """The run.summary payload for a run's events. Raises agents.llm.LLMError if the summary call fails."""
    model = model or os.environ.get("OBSERVER_SUMMARY_MODEL") or None
    run = digest(events)

    async def baseline() -> list[str]:
        if run["baseline"] or not run["seed"]:
            return run["baseline"]
        try:
            return await run_baseline(run["seed"], run["constraints"], model=model)
        except Exception as e:  # the baseline is a bonus, so the summary goes out without it
            log.warning("baseline failed: %s", e)
            return []

    result, ideas = await asyncio.gather(
        call_json(kind="summary", model=model, system=[Section("rules", RULES)], user=prompt(run), out=Summary,
                  mock=lambda: mock_summary(run)),
        baseline(),
    )
    return {**tidy(result.value, run), "baseline": ideas, "model": result.model}

