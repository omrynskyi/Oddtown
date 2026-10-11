import pytest
from pydantic import ValidationError

from observer.events import PAYLOADS, parse_event

MARGUERITE = {
    "id": "char_7f3a",
    "name": "Marguerite Bell",
    "one_liner": "Retired lighthouse keeper who distrusts anything that needs electricity",
    "traits": {"time_horizon": 0.9, "tech_attitude": -0.8, "scale": -0.6, "sociality": -0.4, "risk": 0.2, "rationality": -0.3},
    "model": "some-model",
    "red_line": "Never backs an idea that needs an app",
}

SAMPLE_PAYLOADS = {
    "run.created": {"seed": "a coffee subscription"},
    "agent.born": {"persona": MARGUERITE},
    "run.ready": {"agent_ids": ["char_7f3a", "char_19bc"]},
    "run.started": {"quorum": 2},
    "agent.decision": {"action": "twist", "say": "Beans in an oak chest, once a year.", "reason": "Things should outlast you"},
    "branch.created": {"branch_id": "br_042", "parent_ids": ["br_017"], "op": "twist", "authors": ["char_7f3a"], "text": "Coffee beans delivered once a year in an oak chest"},
    "endorse": {"branch_id": "br_042"},
    "unendorse": {"branch_id": "br_042"},
    "branch.withdraw": {"branch_id": "br_042", "why": "unliked"},
    "world.event": {"kind": "constraint", "text": "It has to work with no electricity"},
    "agent.last_words": {"text": "Keep the chest. Lose the app."},
    "agent.died": {"picked": ["char_19bc", "char_2d4e", "char_88a1"]},
    "run.ended": {
        "finalists": [{"branch_id": "br_042", "text": "Oak chest of beans", "champions": ["char_7f3a"], "minority_report": "No delivery plan"}],
        "baseline": ["Idea one", "Idea two", "Idea three"],
    },
    "run.summary": {
        "headline": "The town wants a chest that lasts.",
        "verdicts": [{"branch_id": "br_042", "title": "The oak chest", "why": "Everyone remembers it", "risk": "Shipping"}],
        "buckets": [{"name": "Built to last", "emoji": "🌳", "blurb": "Things you keep", "branch_ids": ["br_042"]}],
        "moments": ["Dez twisted it into a chest."],
        "baseline": ["Idea one"],
    },
}


def event(type, payload, **overrides):
    return {"run_id": "run_1", "ts": 1760173200.0, "type": type, "agent_id": "char_7f3a", "payload": payload, **overrides}


def test_every_known_type_has_a_sample():
    assert set(SAMPLE_PAYLOADS) == set(PAYLOADS)


@pytest.mark.parametrize("type", sorted(SAMPLE_PAYLOADS))
def test_sample_of_every_type_validates(type):
    parsed = parse_event(event(type, SAMPLE_PAYLOADS[type]))
    assert parsed.type == type
    assert parsed.seq is None


def test_unknown_type_is_stored_as_is():
    parsed = parse_event(event("idea.share", {"anything": [1, 2, 3]}))
    assert parsed.payload == {"anything": [1, 2, 3]}


def test_payloads_allow_extra_fields():
    payload = {**SAMPLE_PAYLOADS["agent.decision"], "retrieved": ["obs_1", "obs_2"]}
    parse_event(event("agent.decision", payload))


@pytest.mark.parametrize("missing", ["run_id", "ts", "type"])
def test_envelope_with_missing_field_is_rejected(missing):
    data = event("run.started", {})
    del data[missing]
    with pytest.raises(ValidationError):
        parse_event(data)


def test_envelope_with_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        parse_event(event("run.started", {}, extra="nope"))


@pytest.mark.parametrize(
    "type, payload",
    [
        ("agent.decision", {"action": "twist", "say": "x" * 101}),
        ("agent.decision", {"action": "dance", "say": "hi"}),
        ("agent.born", {"persona": {**MARGUERITE, "traits": {**MARGUERITE["traits"], "risk": 1.5}}}),
        ("branch.created", {"branch_id": "br_1", "parent_ids": [], "op": "twist", "authors": [], "text": "x"}),
        ("agent.died", {"picked": ["a", "b", "c", "d"]}),
        ("world.event", {"kind": "earthquake", "text": "x"}),
    ],
)
def test_bad_payload_is_rejected(type, payload):
    with pytest.raises(ValidationError):
        parse_event(event(type, payload))
