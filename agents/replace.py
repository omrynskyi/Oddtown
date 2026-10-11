"""Designing a replacement: one survivor proposes, two refine, the last one births it.

Nothing votes and nothing loops, so a replacement costs three design calls plus
the content check. These functions only think. agents.character does the
messaging (replace.request, persona.draft, spawn.request).
"""

from __future__ import annotations

import random
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


class Refined(BaseModel):
    draft: PersonaDraft
    change: str = Field(description="The one change you made, in one line")


class ContentCheck(BaseModel):
    ok: bool
    reason: str


DESIGN_RULES = """A good character has a strong worldview with values that are coherent and clash with the rest of the town, never random jokes. Joke characters produce gimmicky ideas. Give them an ordinary or unusual job, an era or a place, and an obsession that shapes how they judge every idea. Never base a character on a real person, and never build one on a caricature of race, religion, ethnicity, gender, sexuality, disability or any other protected trait. Traits run from -1 to 1."""


def town_lines(personas: list[dict]) -> str:
    return "\n".join(f"- {persona_summary(p)}" for p in personas) or "- nobody"


def propose_prompt(me: dict, town: list[dict], replaces_name: str, briefing: dict) -> tuple[list[Section], list[Section]]:
    task = (
        f"{replaces_name} has died, and you were picked to propose their replacement. "
        "Design a newcomer who is as unlike everyone living in the town as possible, yourself included, "
        "so the town keeps hearing new perspectives. Write the full persona."
    )
    return system_sections(me), [
        Section("world", f"Seed idea the town is working on: {briefing.get('seed') or '(unknown)'}"),
        Section("town", "The living town:\n" + town_lines(town)),
        Section("design_rules", DESIGN_RULES),
        Section("task", task),
    ]


def refine_prompt(
    me: dict, draft: dict, town: list[dict], replaces_name: str, changes: list[str]
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
    return system_sections(me), [
        Section("town", "The living town:\n" + town_lines(town)),
        Section("draft", f"The draft so far: {shown}\nChanges made so far:\n{earlier}"),
        Section("design_rules", DESIGN_RULES),
        Section("task", task),
    ]


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
