import pytest
from fastapi.testclient import TestClient

from observer.app import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(str(tmp_path / "observer.db"))) as c:
        yield c


def post(client, run_id, type="endorse", payload=None, ts=1760173200.0):
    payload = {"branch_id": "br_1"} if payload is None else payload
    return client.post("/events", json={"run_id": run_id, "ts": ts, "type": type, "agent_id": "char_1", "payload": payload})


def test_seq_counts_up_per_run(client):
    assert [post(client, "a").json()["seq"] for _ in range(3)] == [1, 2, 3]
    assert [post(client, "b").json()["seq"] for _ in range(2)] == [1, 2]
    assert post(client, "a").json()["seq"] == 4


def test_invalid_event_is_rejected_and_not_stored(client):
    response = post(client, "a", "agent.decision", {"action": "dance", "say": "hi"})
    assert response.status_code == 422
    assert client.get("/runs").json() == []


def test_unknown_type_is_stored(client):
    assert post(client, "a", "idea.share", {"note": "hi"}).status_code == 200
    assert client.get("/runs/a/events").json()[0]["payload"] == {"note": "hi"}


def test_events_after(client):
    for _ in range(5):
        post(client, "a")
    events = client.get("/runs/a/events", params={"after": 3}).json()
    assert [e["seq"] for e in events] == [4, 5]
    assert set(events[0]) == {"run_id", "seq", "ts", "type", "agent_id", "payload"}


def test_runs_lists_newest_first_with_seed(client):
    post(client, "old", "run.created", {"seed": "a coffee subscription"}, ts=100.0)
    post(client, "old", ts=101.0)
    post(client, "new", "run.created", {"seed": "a dog park"}, ts=200.0)
    runs = client.get("/runs").json()
    assert [r["run_id"] for r in runs] == ["new", "old"]
    assert runs[1] == {"run_id": "old", "events": 2, "first_ts": 100.0, "last_ts": 101.0, "seed": "a coffee subscription"}


def test_websocket_sends_backlog_then_live(client):
    for _ in range(3):
        post(client, "a")
    with client.websocket_connect("/ws/a") as ws:
        assert [ws.receive_json()["seq"] for _ in range(3)] == [1, 2, 3]
        post(client, "a")
        post(client, "other")
        post(client, "a")
        assert [ws.receive_json()["seq"] for _ in range(2)] == [4, 5]


def test_websocket_after_skips_the_start(client):
    for _ in range(4):
        post(client, "a")
    with client.websocket_connect("/ws/a?after=2") as ws:
        assert [ws.receive_json()["seq"] for _ in range(2)] == [3, 4]
