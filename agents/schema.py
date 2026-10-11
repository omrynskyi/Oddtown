"""Persona and branch schemas, plus the schema check every persona passes before it goes live.

The observer's `observer.events.Persona` only checks the fields the UI draws.
This is the full persona. A persona may carry extra fields; a newcomer's
`briefing` (seed, constraints, branches in play) rides along this way.
"""

from __future__ import annotations

import math
import re
import secrets
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents import config

TRAIT_AXES = ("time_horizon", "tech_attitude", "scale", "sociality", "risk", "rationality")

# Same rule as world/launcher.py, since the id becomes a file name and a SLIM name.
AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Traits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time_horizon: float = Field(ge=-1, le=1)
    tech_attitude: float = Field(ge=-1, le=1)
    scale: float = Field(ge=-1, le=1)
    sociality: float = Field(ge=-1, le=1)
    risk: float = Field(ge=-1, le=1)
    rationality: float = Field(ge=-1, le=1)


class Lineage(BaseModel):
    generation: int = Field(default=0, ge=0)
    designed_by: list[str] = Field(default_factory=list)
    replaces: str | None = None


class Lifespan(BaseModel):
    max_actions: int = Field(default=config.MAX_ACTIONS, ge=1)
    born_at: str | None = None


class Persona(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str = Field(min_length=1, max_length=80)
    one_liner: str = Field(min_length=1, max_length=200)
    worldview: str = Field(min_length=1, max_length=400)
    loves: list[str] = Field(min_length=1, max_length=6)
    hates: list[str] = Field(min_length=1, max_length=6)
    red_line: str = Field(min_length=1, max_length=200)
    voice: str = Field(min_length=1, max_length=200)
    traits: Traits
    model: str
    lineage: Lineage = Field(default_factory=Lineage)
    lifespan: Lifespan = Field(default_factory=Lifespan)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not AGENT_ID.match(value):
            raise ValueError("id must be 1 to 64 letters, digits, _ or -, starting with a letter or digit")
        return value

    @field_validator("model")
    @classmethod
    def _model(cls, value: str) -> str:
        if value != config.MOCK_MODEL and value not in config.allowed_models():
            raise ValueError(f"model {value!r} is not configured, expected one of {config.allowed_models()}")
        return value


class Branch(BaseModel):
    """One version of the idea. Branches never change once made."""

    model_config = ConfigDict(extra="allow")

    branch_id: str
    parent_ids: list[str] = Field(default_factory=list)
    op: Literal["seed", "twist", "merge"]
    author: str | None = None
    authors: list[str] = Field(min_length=1)
    text: str = Field(min_length=1)
    created_at: str | None = None


def validate_persona(data: dict) -> Persona:
    """The schema check. Raises pydantic.ValidationError with every problem found."""
    return Persona.model_validate(data)


def traits_vector(traits: dict | Traits) -> list[float]:
    t = traits.model_dump() if isinstance(traits, Traits) else traits
    return [float(t.get(axis, 0.0)) for axis in TRAIT_AXES]


def trait_distance(a: dict | Traits, b: dict | Traits) -> float:
    return math.dist(traits_vector(a), traits_vector(b))


def nearest(traits: dict, others: list[dict]) -> tuple[dict | None, float]:
    """The persona in `others` whose traits sit closest to `traits`, and how far."""
    best, best_d = None, float("inf")
    for other in others:
        d = trait_distance(traits, other.get("traits", {}))
        if d < best_d:
            best, best_d = other, d
    return best, best_d


def clamp_traits(traits: dict) -> dict:
    return {axis: max(-1.0, min(1.0, round(float(traits.get(axis, 0.0)), 2))) for axis in TRAIT_AXES}


def new_agent_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:16] or "newcomer"
    return f"char_{slug}_{secrets.token_hex(2)}"


def new_branch_id() -> str:
    return f"br_{secrets.token_hex(3)}"


def persona_summary(persona: dict) -> str:
    """One line per persona, for prompts that need to see the whole town."""
    traits = ", ".join(f"{axis} {persona.get('traits', {}).get(axis, 0):+.1f}" for axis in TRAIT_AXES)
    return f"{persona.get('name')} ({persona.get('id')}): {persona.get('one_liner')}. Red line: {persona.get('red_line')}. Traits: {traits}"


def public_persona(persona: dict) -> dict:
    """The persona without the newcomer briefing, for the Directory record and prompts."""
    return {k: v for k, v in persona.items() if k != "briefing"}
