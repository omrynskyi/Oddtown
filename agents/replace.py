"""Designing a replacement: one survivor proposes, two refine, the last one births it.

Nothing votes and nothing loops, so a replacement costs three design calls plus
the content check. These functions only think. agents.character does the
messaging (replace.request, persona.draft, spawn.request).
"""

from __future__ import annotations

import random
import re
from typing import Literal

from pydantic import BaseModel, Field

from agents import config
from agents.brain import clip, system_sections
from agents.llm import Section
from agents.schema import TRAIT_AXES, clamp_traits, nearest, new_agent_id, now_iso, persona_summary


class DraftTraits(BaseModel):
    time_horizon: float = Field(description="-1 thinks in minutes, +1 thinks in centuries")
    tech_attitude: float = Field(description="-1 wants nothing with a plug, +1 wants everything automated")
    scale: float = Field(description="-1 one person, one street, +1 the whole planet")
    sociality: float = Field(description="-1 hermit, +1 needs a crowd")
    risk: float = Field(description="-1 changes nothing without proof, +1 bets everything on a hunch")
    rationality: float = Field(description="-1 gut feelings and omens, +1 only logic and evidence")


class PersonaDraft(BaseModel):
    name: str
    one_liner: str = Field(description="Who they are, in one line")
    worldview: str = Field(description="One or two sentences")
    loves: list[str] = Field(description="Three things")
    hates: list[str] = Field(description="Three things")
    red_line: str = Field(description="One thing they will never back")
    voice: str = Field(description="How they talk")
    traits: DraftTraits


class Proposal(BaseModel):
    concepts: list[str] = Field(description="Three newcomer concepts from walks of life far apart from each other and from the town, one line each")
    names: list[str] = Field(description="Five possible full names for the newcomer, unlike each other and unlike the names already used")
    draft: PersonaDraft = Field(description="The full persona for the concept least like anyone in town")


def free_name(name: str, options: list[str], taken: list[str]) -> str:
    """The draft's name if nobody has used any part of it yet, else the first option that's free."""
    for candidate in [name, *options]:
        if candidate and candidate.strip() and not name_clash(candidate, taken):
            return clip(candidate.strip(), 80)
    return name


class Refined(BaseModel):
    draft: PersonaDraft
    change: str = Field(description="The one change you made, in one line")


class ContentCheck(BaseModel):
    ok: bool
    reason: str


DESIGN_RULES = """A good character has a strong worldview with values that are coherent and clash with the rest of the town, never random jokes. Joke characters produce gimmicky ideas. Give them an ordinary or unusual job, an era or a place, and an obsession that shapes how they judge every idea. Never base a character on a real person, and never build one on a caricature of race, religion, ethnicity, gender, sexuality, disability or any other protected trait. Traits run from -1 to 1."""


_STOP = set("the and for with that this who their they them from into every anything everything nothing what only".split())


def persona_words(p: dict) -> set[str]:
    text = " ".join([p.get("one_liner", ""), p.get("worldview", ""), *p.get("loves", []), *p.get("hates", [])])
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _STOP}


def echoes(draft: dict, people: list[dict]) -> tuple[dict | None, list[str]]:
    """The person whose one-liner, worldview, loves and hates share the most words with the draft."""
    mine = persona_words(draft)
    best, shared = None, set()
    for p in people:
        both = mine & persona_words(p)
        if len(both) > len(shared):
            best, shared = p, both
    return best, sorted(shared)


def name_clash(name: str, taken: list[str]) -> str | None:
    """A taken name that shares a first or last name with this one."""
    parts = {w.lower() for w in name.split() if len(w) > 2}
    return next((t for t in taken if parts & {w.lower() for w in t.split() if len(w) > 2}), None)


def gaps_feedback(draft: dict, people: list[dict], taken: list[str]) -> str:
    """What code can see is too close to someone already in town, for the next designer to fix."""
    lines = []
    clash = name_clash(draft.get("name", ""), [t for t in taken if t != draft.get("name")])
    if clash:
        lines.append(f"The name {draft['name']} shares a name with {clash}, who already lived in this town. Change it.")
    echo, shared = echoes(draft, [p for p in people if p.get("id") != draft.get("id")])
    if echo and len(shared) >= 3:
        lines.append(f"The draft reads a lot like {echo['name']} ({echo.get('one_liner')}): both talk about {', '.join(shared[:6])}. Move it away from that.")
    return "\n".join(lines) or "Nothing in the town reads like this draft yet."


def town_lines(personas: list[dict]) -> str:
    return "\n".join(f"- {persona_summary(p)}" for p in personas) or "- nobody"


def empty_corner(town: list[dict], rng: random.Random, samples: int = 3000) -> dict:
    """The point in trait space farthest from every living character, by sampling."""
    best, best_d = {axis: 0.0 for axis in TRAIT_AXES}, -1.0
    for _ in range(samples):
        point = {axis: round(rng.uniform(-1, 1), 1) for axis in TRAIT_AXES}
        d = nearest(point, town)[1] if town else 0.0
        if d > best_d:
            best, best_d = point, d
    return best


def propose_prompt(
    me: dict, town: list[dict], replaces_name: str, briefing: dict,
    taken: list[str] | None = None, target: dict | None = None,
) -> tuple[list[Section], list[Section]]:
    task = (
        f"{replaces_name} has died, and you were picked to propose their replacement. "
        "Design a newcomer who is as unlike everyone living in the town as possible, yourself included, "
        "so the town keeps hearing new perspectives. Write the full persona."
    )
    sections = [
        Section("world", f"Seed idea the town is working on: {briefing.get('seed') or '(unknown)'}"),
        Section("town", "The living town:\n" + town_lines(town)),
    ]
    if taken is not None and target is not None:
        corner = ", ".join(f"{axis} {target[axis]:+.1f}" for axis in TRAIT_AXES)
        sections.append(Section("gaps", (
            f"Names already used in this town, living or dead: {', '.join(taken) or 'none'}. Pick a name that shares no part with them.\n"
            f"Nobody in town sits near these traits: {corner}. Design the newcomer for them."
        )))
    return system_sections(me), sections + [Section("design_rules", DESIGN_RULES), Section("task", task)]


def refine_prompt(
    me: dict, draft: dict, town: list[dict], replaces_name: str, changes: list[str], feedback: str | None = None
) -> tuple[list[Section], list[Section]]:
    closest, distance = nearest(draft["traits"], town)
    closest_line = (
        f"By traits, the draft sits closest to {closest['name']} (distance {distance:.2f}). Push it further away from them."
        if closest
        else "Push it away from the living town."
    )
    earlier = "\n".join(f"- {c}" for c in changes) or "- none"
    task = (
        f"You were picked to refine the replacement for {replaces_name}. Change the draft once, through your own "
        f"worldview, and return the whole revised persona. {closest_line} Keep it coherent."
    )
    shown = {k: draft[k] for k in ("name", "one_liner", "worldview", "loves", "hates", "red_line", "voice", "traits")}
    sections = [
        Section("town", "The living town:\n" + town_lines(town)),
        Section("draft", f"The draft so far: {shown}\nChanges made so far:\n{earlier}"),
    ]
    if feedback:
        sections.append(Section("gaps", feedback))
    return system_sections(me), sections + [Section("design_rules", DESIGN_RULES), Section("task", task)]


def content_check_prompt(persona: dict) -> tuple[list[Section], list[Section]]:
    rules = (
        "You check characters before they appear in a public demo. Reject a character only if it depicts a real "
        "person, uses slurs, or is a caricature built on race, religion, ethnicity, gender, sexuality, disability or "
        "another protected trait. Strong opinions, odd jobs and abrasive voices are fine."
    )
    shown = {k: persona.get(k) for k in ("name", "one_liner", "worldview", "loves", "hates", "red_line", "voice")}
    return [Section("rules", rules)], [Section("persona", f"Character: {shown}"), Section("task", "Is this character ok?")]


def to_persona(
    draft: PersonaDraft, *, model: str, replaces: str, designers: list[str], generation: int, persona_id: str | None = None
) -> dict:
    """A full persona from a draft. Ids, model, lineage and lifespan are filled in by code, not the LLM."""
    return {
        "id": persona_id or new_agent_id(draft.name),
        "name": clip(draft.name, 80),
        "one_liner": clip(draft.one_liner, 200),
        "worldview": clip(draft.worldview, 400),
        "loves": [clip(x, 80) for x in draft.loves[:6]] or ["surprises"],
        "hates": [clip(x, 80) for x in draft.hates[:6]] or ["sameness"],
        "red_line": clip(draft.red_line, 200),
        "voice": clip(draft.voice, 200),
        "traits": clamp_traits(draft.traits.model_dump()),
        "model": model,
        "lineage": {"generation": generation, "designed_by": designers, "replaces": replaces},
        "lifespan": {"max_actions": config.MAX_ACTIONS, "born_at": now_iso()},
    }


def to_draft(persona: dict) -> PersonaDraft:
    return PersonaDraft.model_validate({**persona, "traits": clamp_traits(persona["traits"])})


# -- the mock designers -------------------------------------------------------

FIRST = ["Ines", "Bartholomew", "Yuki", "Oluwaseun", "Greta", "Mateo", "Saoirse", "Kofi", "Lin", "Ezra", "Noor", "Wim"]
JOBS = [
    "night-shift air traffic controller", "medieval manuscript restorer", "competitive cheese judge",
    "arctic weather station cook", "retired circus rigger", "volcano tour guide", "beekeeper on a rooftop",
    "court stenographer", "lighthouse engineer turned baker", "glacier surveyor", "toy repair surgeon",
]
OBSESSIONS = [
    ("checklists", "improvising"), ("silence", "notifications"), ("handwriting", "passwords"),
    ("fermentation", "anything instant"), ("maps", "vague directions"), ("thrift", "waste"),
    ("rituals", "rushing"), ("weather", "indoor life"), ("puzzles", "obvious answers"),
]


def mock_propose(town: list[dict], rng: random.Random) -> PersonaDraft:
    avg = {axis: sum(p["traits"][axis] for p in town) / len(town) if town else 0.0 for axis in TRAIT_AXES}
    traits = {axis: max(-1.0, min(1.0, round(-avg[axis] + rng.uniform(-0.3, 0.3), 2))) for axis in TRAIT_AXES}
    love, hate = rng.choice(OBSESSIONS)
    job = rng.choice(JOBS)
    return PersonaDraft(
        name=f"{rng.choice(FIRST)} {rng.choice(['Vale', 'Okafor', 'Brandt', 'Moss', 'Ilunga', 'Sato', 'Quist'])}",
        one_liner=job[0].upper() + job[1:],
        worldview=f"Everything worth doing comes down to {love}.",
        loves=[love, "small details", "honest work"],
        hates=[hate, "hype", "shortcuts"],
        red_line=f"Never backs anything built on {hate}",
        voice="Measured and dry, with oddly specific comparisons",
        traits=DraftTraits(**traits),
    )


def mock_refine(draft: dict, town: list[dict], rng: random.Random) -> Refined:
    closest, _ = nearest(draft["traits"], town)
    traits = dict(draft["traits"])
    if closest:
        for axis in TRAIT_AXES:
            gap = traits[axis] - closest["traits"][axis]
            traits[axis] = max(-1.0, min(1.0, round(traits[axis] + (0.2 if gap >= 0 else -0.2), 2)))
    revised = to_draft({**draft, "traits": traits})
    return Refined(draft=revised, change=f"Pushed the traits away from {closest['name'] if closest else 'the town'}")
