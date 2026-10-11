"""Tunable numbers for the agent brain, from the plan's starting parameters.

Every value can be overridden from the environment, so tests and rehearsals can
shrink a run without touching code:

    ODDTOWN_MODEL        force every character onto one model, e.g. "mock" for runs with no API key
    ODDTOWN_MODELS       comma-separated models a persona may name (default: big-pickle)
    OPENCODE_API_KEY     key for OpenCode Zen, where big-pickle runs
    ODDTOWN_LLM_BASE_URL OpenAI-compatible endpoint for non-Claude models (default: OpenCode Zen)
    ODDTOWN_REASONING_EFFORT  reasoning_effort sent to that endpoint, e.g. "none" so a local Ollama
                         model answers in seconds instead of thinking first (default: not sent)
    ODDTOWN_TIME_SCALE   multiplies every wait and wall-clock lifespan, e.g. 0.1 for a 10x faster town
    ODDTOWN_MAX_ACTIONS  lifespan in actions
    ODDTOWN_LIFESPAN_S   lifespan in wall-clock seconds, before scaling
"""

from __future__ import annotations

import os


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


DEFAULT_MODEL = "big-pickle"
MOCK_MODEL = "mock"

# Models that aren't Claude go to an OpenAI-compatible chat completions endpoint.
LLM_BASE_URL = os.environ.get("ODDTOWN_LLM_BASE_URL", "https://opencode.ai/zen/v1")
LLM_API_KEY_ENV = "OPENCODE_API_KEY"
# Thinking models on Ollama (qwen3.6, nemotron3) think by default: 25+ seconds a call, and sometimes
# they run out of tokens before answering. "none" turns that off. Unset sends nothing, as before.
LLM_REASONING_EFFORT = os.environ.get("ODDTOWN_REASONING_EFFORT") or None

# Lifespan and turnover
MAX_ACTIONS = _int("ODDTOWN_MAX_ACTIONS", 40)
LIFESPAN_S = _float("ODDTOWN_LIFESPAN_S", 8 * 60)
STAGGER = (0.5, 1.0)  # starting lifespans as a share of the max, so deaths don't bunch up

# Endorsements and gossip
MAX_ENDORSEMENTS = 2
BATCH_WINDOW_S = 3.0
IDLE_S = (20.0, 40.0)
NEWCOMER_FIRST_TURN_S = (3.0, 8.0)

# Memory
RETRIEVE_TOP_K = 8
RECENCY_DECAY = 0.99
REFLECT_AT_IMPORTANCE = 40
DEFAULT_IMPORTANCE = 5
MAX_SCORED_PER_CALL = 20  # older unscored observations keep the default score
MAX_BRANCHES_IN_PROMPT = 12

# Stopping: broadcast rest once no branch has gained or lost a living backer for this long
REST_AFTER_S = _float("ODDTOWN_REST_AFTER_S", 120.0)

# Replacement design
DESIGN_TIMEOUT_S = 90.0  # a refiner takes over if the designer before it goes quiet and dies

# Text limits from the observer contract
SAY_MAX = 100
LAST_WORDS_MAX = 280
BRANCH_TEXT_MAX = 280

# Effort per call kind, for Claude models. Decisions are frequent and latency-sensitive, design is rare.
EFFORT = {
    "decision": os.environ.get("ODDTOWN_EFFORT_DECISION", "low"),
    "reflection": os.environ.get("ODDTOWN_EFFORT_REFLECTION", "low"),
    "last_words": "low",
    "propose": os.environ.get("ODDTOWN_EFFORT_DESIGN", "medium"),
    "refine": os.environ.get("ODDTOWN_EFFORT_DESIGN", "medium"),
    "content_check": "low",
    "baseline": os.environ.get("ODDTOWN_EFFORT_BASELINE", "medium"),
}

# Output caps per call kind. Reasoning models spend part of this thinking, so leave room.
# big-pickle allows up to 32,000.
MAX_TOKENS = {
    "decision": 8000,
    "reflection": 4000,
    "last_words": 3000,
    "propose": 12000,
    "refine": 12000,
    "content_check": 3000,
    "baseline": 12000,
}


def time_scale() -> float:
    return _float("ODDTOWN_TIME_SCALE", 1.0)


def scaled(seconds: float) -> float:
    return seconds * time_scale()


def model_override() -> str | None:
    return os.environ.get("ODDTOWN_MODEL") or None


def allowed_models() -> list[str]:
    models = [m.strip() for m in os.environ.get("ODDTOWN_MODELS", DEFAULT_MODEL).split(",") if m.strip()]
    return models or [DEFAULT_MODEL]
