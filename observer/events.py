"""Observer event contract.

Every event the observer stores shares one envelope:

    {"run_id": "run_1", "seq": 7, "ts": 1760173200.5, "type": "agent.decision",
     "agent_id": "char_7f3a", "payload": {...}}

Senders leave out `seq`; the observer assigns it so every run has one order.
`ts` is Unix seconds from the sender's clock.

Known types have a payload model below, and their payloads are checked. Any
other type, like `idea.share` or `human.request`, is stored with its payload
as-is. Payload models allow extra fields, so agents can attach more detail
without breaking anything.

The full persona rules live in Anthony's `agents/schema.py`. The `Persona`
model here only checks the fields the UI draws.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TRAIT_AXES = ("time_horizon", "tech_attitude", "scale", "sociality", "risk", "rationality")


class Payload(BaseModel):
    model_config = ConfigDict(extra="allow")


class Traits(BaseModel):
    time_horizon: float = Field(ge=-1, le=1)
    tech_attitude: float = Field(ge=-1, le=1)
    scale: float = Field(ge=-1, le=1)
    sociality: float = Field(ge=-1, le=1)
    risk: float = Field(ge=-1, le=1)
    rationality: float = Field(ge=-1, le=1)


class Persona(Payload):
    id: str
    name: str
    one_liner: str
    traits: Traits
    model: str | None = None


class RunCreated(Payload):
    seed: str


class AgentBorn(Payload):
    persona: Persona


class RunReady(Payload):
    agent_ids: list[str]


class RunStarted(Payload):
    quorum: int | None = None  # backers besides its authors that make a branch a proposal; None when proposals are off


class AgentDecision(Payload):
    action: Literal["twist", "merge", "back", "trash", "pitch", "ignore"]
    say: str = Field(max_length=100)  # the speech bubble text
    reason: str = ""


class BranchCreated(Payload):
    branch_id: str
    parent_ids: list[str]
    op: Literal["seed", "twist", "merge"]
    authors: list[str] = Field(min_length=1)
    text: str


class Endorse(Payload):
    branch_id: str


class BranchWithdraw(Payload):
    """An author retired one of its unbacked branches, and every character composts it until someone backs it."""

    branch_id: str
    why: Literal["unliked", "budget", "died"] | None = None  # everyone it was shown to turned it down, over budget, author died


class WorldEvent(Payload):
    kind: Literal["seed", "constraint", "feedback"]
    text: str


class AgentLastWords(Payload):
    text: str = Field(max_length=280)


class AgentDied(Payload):
    picked: list[str] = Field(max_length=3)  # empty when a peer noticed a crash


class Finalist(Payload):
    branch_id: str
    text: str
    champions: list[str]
    minority_report: str = ""


class RunEnded(Payload):
    finalists: list[Finalist] = Field(max_length=3)
    baseline: list[str]


class Verdict(Payload):
    branch_id: str
    title: str
    why: str = ""
    risk: str = ""


class Bucket(Payload):
    name: str
    emoji: str = ""
    blurb: str = ""
    branch_ids: list[str]


class RunSummary(Payload):
    """The observer's own end-of-run summary. On a failed call only `error` is set."""

    headline: str = ""
    verdicts: list[Verdict] = Field(default_factory=list, max_length=3)
    buckets: list[Bucket] = Field(default_factory=list)
    moments: list[str] = Field(default_factory=list)
    baseline: list[str] = Field(default_factory=list)
    model: str | None = None
    error: str | None = None


PAYLOADS: dict[str, type[Payload]] = {
    "run.created": RunCreated,
    "agent.born": AgentBorn,
    "run.ready": RunReady,
    "run.started": RunStarted,
    "agent.decision": AgentDecision,
    "branch.created": BranchCreated,
    "endorse": Endorse,
    "unendorse": Endorse,
    "branch.withdraw": BranchWithdraw,
    "world.event": WorldEvent,
    "agent.last_words": AgentLastWords,
    "agent.died": AgentDied,
    "run.ended": RunEnded,
    "run.summary": RunSummary,
}


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    seq: int | None = None
    ts: float
    type: str = Field(min_length=1)
    agent_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


def parse_event(data: dict[str, Any]) -> Event:
    """Validate an event. Raises pydantic.ValidationError if it breaks the contract."""
    event = Event.model_validate(data)
    model = PAYLOADS.get(event.type)
    if model is not None:
        model.model_validate(event.payload)
    return event
