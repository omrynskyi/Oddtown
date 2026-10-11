import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from observer.app import create_app
from observer.events import parse_event
from observer.summary import digest, summarize

FIXTURE = Path(__file__).resolve().parents[2] / "ui" / "src" / "fixtures" / "mock-run.json"


@pytest.fixture(autouse=True)
def mock_model(monkeypatch):
    monkeypatch.setenv("ODDTOWN_MODEL", "mock")


@pytest.fixture
def events():
    return json.loads(FIXTURE.read_text())


def test_digest_counts_living_support(events):
    run = digest(events)
    assert run["seed"] == "a coffee subscription"
    assert run["names"]["char_marg"]
    assert all(agent not in run["deaths"] for backers in run["support"].values() for agent in backers)


def test_mock_summary_fits_the_contract(events):
    payload = asyncio.run(summarize(events))
    parse_event({"run_id": "r", "ts": 0, "type": "run.summary", "payload": payload})
    branches = {e["payload"]["branch_id"] for e in events if e["type"] == "branch.created"}
    assert payload["headline"] and payload["moments"]
    assert 1 <= len(payload["verdicts"]) <= 3
    assert {v["branch_id"] for v in payload["verdicts"]} <= branches
    bucketed = [b for bucket in payload["buckets"] for b in bucket["branch_ids"]]
    assert len(bucketed) == len(set(bucketed)) and set(bucketed) <= branches
    assert len(payload["baseline"]) == 3


def test_summary_endpoint_stores_a_run_summary(tmp_path, events):
    with TestClient(create_app(str(tmp_path / "observer.db"), auto_summary=False)) as client:
        for e in events:
            client.post("/events", json={k: e[k] for k in ("run_id", "ts", "type", "agent_id", "payload")})
        assert client.post("/runs/nope/summary").status_code == 404
        stored = client.post(f"/runs/{events[0]['run_id']}/summary").json()
        assert stored["type"] == "run.summary" and stored["seq"] == len(events) + 1
        assert stored["payload"]["verdicts"]


def test_run_ended_triggers_a_summary(tmp_path, events):
    with TestClient(create_app(str(tmp_path / "observer.db"), auto_summary=True)) as client:
        for e in events:
            client.post("/events", json={k: e[k] for k in ("run_id", "ts", "type", "agent_id", "payload")})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            types = [e["type"] for e in client.get(f"/runs/{events[0]['run_id']}/events").json()]
            if "run.summary" in types:
                break
            time.sleep(0.05)
        assert types[-1] == "run.summary"
