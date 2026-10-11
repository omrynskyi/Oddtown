"""Unit tests for the agent brain: schema, memory, prompts, the mock brain and llm.py."""

import json
import random
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agents import brain, config, llm, replace
from agents.brain import Decision, View
from agents.llm import Section
from agents.memory import MemoryStream
from agents.schema import nearest, trait_distance, validate_persona
from observer.events import parse_event

ROOT = Path(__file__).resolve().parents[2]
CAST = json.loads((ROOT / "personas" / "starting_cast.json").read_text())


# -- schema -------------------------------------------------------------------


def test_starting_cast_passes_the_schema_and_the_observer_contract():
    assert len(CAST) == 6
    assert len({p["id"] for p in CAST}) == 6
    for persona in CAST:
        validate_persona(persona)
        parse_event({"run_id": "r", "ts": 0, "type": "agent.born", "agent_id": persona["id"], "payload": {"persona": persona}})


def test_cast_is_spread_across_trait_space():
    distances = [trait_distance(a["traits"], b["traits"]) for i, a in enumerate(CAST) for b in CAST[i + 1:]]
    assert min(distances) > 0.8


def test_schema_rejects_bad_traits_ids_and_models():
    good = CAST[0]
    with pytest.raises(ValidationError):
        validate_persona({**good, "traits": {**good["traits"], "risk": 1.5}})
    with pytest.raises(ValidationError):
        validate_persona({**good, "id": "has spaces"})
    with pytest.raises(ValidationError):
        validate_persona({**good, "model": "not-a-model"})
    validate_persona({**good, "model": "mock"})


# -- memory -------------------------------------------------------------------


def test_retrieval_prefers_recent_and_important():
    m = MemoryStream()
    old_big = m.add("old but important", "world", importance=10)
    for i in range(20):
        m.step += 1
        m.add(f"filler {i}", "endorsement")
    m.step += 1
    new_small = m.add("brand new", "heard_idea", importance=4)
    top = m.retrieve(k=3)
    assert old_big in top and new_small in top


def test_mechanical_kinds_are_never_sent_for_scoring():
    m = MemoryStream()
    m.add("Dez now backs br_1", "endorsement")
    idea = m.add("Dez shared br_1", "heard_idea")
    assert m.unscored() == [idea]


def test_settle_through_leaves_later_arrivals_for_the_next_call():
    m = MemoryStream()
    first = m.add("a", "heard_idea")
    shown = m.unscored()
    late = m.add("arrived during the call", "heard_idea")
    m.settle_through(shown[-1].seq)
    assert first.scored and not late.scored
    assert m.unscored() == [late]


def test_reflection_triggers_past_the_threshold():
    m = MemoryStream()
    for _ in range(4):
        m.score(m.add("big news", "world").id, 10)
    assert m.wants_reflection()
    m.reflected()
    assert not m.wants_reflection()


# -- prompts and the mock brain -----------------------------------------------


def make_view(**overrides) -> View:
    me, peers = CAST[0], CAST[1:]
    seed = {"branch_id": "br_seed", "parent_ids": [], "op": "seed", "authors": ["world"], "text": "a coffee subscription"}
    twist = {"branch_id": "br_aa", "parent_ids": ["br_seed"], "op": "twist", "authors": [peers[0]["id"]],
             "text": "Coffee delivered by racing pigeon on a fixed weekly route"}
    memory = MemoryStream()
    obs = memory.add(f"{peers[0]['name']} shared br_aa (\"Coffee delivered by…\")", "heard_idea")
    inbox = [{"type": "idea.share", "msg_id": "m1", "from": peers[0]["id"], "body": {"branch": twist, "note": "Fast!"}}]
    values = dict(
        me=me, seed="a coffee subscription", constraints=["it has to work with no electricity"], life_left=5,
        life_max=20, seconds_left=200, agenda="", my_endorsed=[], peers=peers,
        endorsements={peers[1]["id"]: ["br_aa"]}, branches={"br_seed": seed, "br_aa": twist},
        memories=[], inbox=inbox, to_score=[obs],
    )
    values.update(overrides)
    return View(**values)


def test_decision_prompt_sections_are_stable_first_and_show_branch_text_once():
    system, user = brain.decision_prompt(make_view())
    assert [s.name for s in system] == ["rules", "persona"]
    assert [s.name for s in user] == ["world", "people", "branches", "memories", "inbox", "to_score", "task"]
    full = "\n".join(s.text for s in system + user)
    assert full.count("Coffee delivered by racing pigeon on a fixed weekly route") == 1
    assert "msg m1" in full and "it has to work with no electricity" in full


def test_mock_decision_is_valid_and_answers_every_message():
    view = make_view()
    for seed in range(30):
        d = brain.mock_decision(view, random.Random(seed))
        Decision.model_validate(d.model_dump())
        assert [r.msg_id for r in d.replies] == ["m1"]
        if d.target_branch:
            assert d.target_branch in view.branches


def test_mock_designers_make_valid_personas_that_move_away_from_the_town():
    rng = random.Random(1)
    draft = replace.mock_propose(CAST, rng)
    persona = replace.to_persona(draft, model="mock", replaces="char_marg", designers=["a", "b", "c"], generation=1)
    validate_persona(persona)
    _, before = nearest(persona["traits"], CAST)
    refined = replace.mock_refine(persona, CAST, rng)
    after_persona = replace.to_persona(refined.draft, model="mock", replaces="char_marg", designers=["a"], generation=1)
    _, after = nearest(after_persona["traits"], CAST)
    assert after >= before - 1e-9


# -- llm.py -------------------------------------------------------------------


async def test_mock_call_reports_usage_and_sections(monkeypatch):
    monkeypatch.setenv("ODDTOWN_MODEL", "mock")
    view = make_view()
    system, user = brain.decision_prompt(view)
    result = await llm.call_json(kind="decision", model="claude-opus-5-5", system=system, user=user, out=Decision,
                                 mock=lambda: brain.mock_decision(view, random.Random(0)))
    assert result.model == "mock"
    assert result.usage.prompt_tokens > 100 and result.usage.output_tokens > 0
    assert set(result.sections) == {"rules", "persona", "world", "people", "branches", "memories", "inbox", "to_score", "task"}
    assert abs(sum(result.sections.values()) - result.usage.prompt_tokens) <= len(result.sections)


async def test_claude_call_caches_the_persona_and_counts_every_token(monkeypatch):
    monkeypatch.delenv("ODDTOWN_MODEL", raising=False)
    sent = {}
    view = make_view()
    decision = brain.mock_decision(view, random.Random(0))

    class FakeMessages:
        async def parse(self, **kwargs):
            sent.update(kwargs)
            usage = SimpleNamespace(input_tokens=200, output_tokens=150, cache_read_input_tokens=900, cache_creation_input_tokens=0)
            return SimpleNamespace(usage=usage, stop_reason="end_turn", parsed_output=decision, model="claude-opus-5-5")

    monkeypatch.setattr(llm, "_client", SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages())))
    system, user = brain.decision_prompt(view)
    result = await llm.call_json(kind="decision", model="claude-opus-5-5", system=system, user=user, out=Decision, mock=None)

    assert result.value == decision
    assert sent["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in sent["system"][0]
    assert sent["output_format"] is Decision
    assert sent["output_config"] == {"effort": config.EFFORT["decision"]}
    assert sent["fallbacks"] == "default" and sent["betas"] == [llm.FALLBACK_BETA]
    assert result.usage.prompt_tokens == 1100 and result.usage.total == 1250


async def test_claude_refusal_raises(monkeypatch):
    monkeypatch.delenv("ODDTOWN_MODEL", raising=False)

    class FakeMessages:
        async def parse(self, **kwargs):
            usage = SimpleNamespace(input_tokens=10, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0)
            return SimpleNamespace(usage=usage, stop_reason="refusal", parsed_output=None, model=kwargs["model"])

    monkeypatch.setattr(llm, "_client", SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages())))
    with pytest.raises(llm.LLMError):
        await llm.call_json(kind="decision", model="claude-opus-5-5", system=[Section("rules", "r")],
                            user=[Section("task", "t")], out=Decision, mock=None)


# -- llm.py, OpenAI-compatible (big-pickle) ------------------------------------


def fake_endpoint(monkeypatch, replies):
    """Point llm.py at a fake chat completions server that answers with `replies` in order."""
    import httpx

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status, content = replies[min(len(seen), len(replies)) - 1]
        if status != 200:
            return httpx.Response(status, json={"error": {"message": content}})
        usage = {"prompt_tokens": 1000, "completion_tokens": 300, "prompt_tokens_details": {"cached_tokens": 600}}
        return httpx.Response(200, json={"model": "big-pickle", "usage": usage,
                                         "choices": [{"message": {"content": content}, "finish_reason": "stop"}]})

    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")
    monkeypatch.delenv("ODDTOWN_MODEL", raising=False)
    # tests/conftest.py loads .env, so pin the defaults whatever endpoint this machine points at.
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://opencode.ai/zen/v1")
    monkeypatch.setattr(config, "LLM_REASONING_EFFORT", None)
    monkeypatch.setattr(llm, "_http", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(llm, "_json_mode", True)
    return seen


async def test_big_pickle_request_shape_usage_and_fenced_json(monkeypatch):
    view = make_view()
    decision = brain.mock_decision(view, random.Random(0))
    seen = fake_endpoint(monkeypatch, [(200, f"Here you go:\n```json\n{decision.model_dump_json()}\n```")])
    system, user = brain.decision_prompt(view)
    result = await llm.call_json(kind="decision", model="big-pickle", system=system, user=user, out=Decision, mock=None)

    assert result.value == decision and result.model == "big-pickle" and result.retries == 0
    request = seen[0]
    body = json.loads(request.content)
    assert str(request.url) == "https://opencode.ai/zen/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    assert body["model"] == "big-pickle" and body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system" and "Red line" in body["messages"][0]["content"]
    assert '"say"' in body["messages"][1]["content"]  # the schema rides at the end of the user message
    assert result.usage.input_tokens == 400 and result.usage.cache_read == 600 and result.usage.total == 1300
    assert "format" in result.sections


async def test_reasoning_effort_is_sent_only_when_set(monkeypatch):
    reply = brain.mock_decision(make_view(), random.Random(0)).model_dump_json()
    seen = fake_endpoint(monkeypatch, [(200, reply)])
    system, user = brain.decision_prompt(make_view())
    await llm.call_json(kind="decision", model="big-pickle", system=system, user=user, out=Decision, mock=None)
    assert "reasoning_effort" not in json.loads(seen[0].content)

    monkeypatch.setattr(config, "LLM_REASONING_EFFORT", "none")
    await llm.call_json(kind="decision", model="big-pickle", system=system, user=user, out=Decision, mock=None)
    assert json.loads(seen[1].content)["reasoning_effort"] == "none"


async def test_big_pickle_falls_back_when_json_mode_is_rejected(monkeypatch):
    seen = fake_endpoint(monkeypatch, [(400, "response_format not supported"), (200, '{"text": "So long."}')])
    result = await llm.call_json(kind="last_words", model="big-pickle", system=[Section("rules", "r")],
                                 user=[Section("task", "t")], out=brain.LastWords, mock=None)
    assert result.value.text == "So long."
    assert "response_format" not in json.loads(seen[1].content)
    assert llm._json_mode is False


async def test_big_pickle_retries_once_when_the_reply_does_not_fit(monkeypatch):
    seen = fake_endpoint(monkeypatch, [(200, "I think they should..."), (200, '{"text": "Fine."}')])
    result = await llm.call_json(kind="last_words", model="big-pickle", system=[Section("rules", "r")],
                                 user=[Section("task", "t")], out=brain.LastWords, mock=None)
    assert result.value.text == "Fine." and result.retries == 1
    retry = json.loads(seen[1].content)["messages"]
    assert retry[-2]["role"] == "assistant" and "didn't fit the schema" in retry[-1]["content"]


async def test_big_pickle_without_a_key_fails_clearly(monkeypatch):
    fake_endpoint(monkeypatch, [(200, "{}")])
    monkeypatch.delenv("OPENCODE_API_KEY")
    with pytest.raises(llm.LLMError, match="OPENCODE_API_KEY"):
        await llm.call_json(kind="last_words", model="big-pickle", system=[Section("rules", "r")],
                            user=[Section("task", "t")], out=brain.LastWords, mock=None)
