import json
import time
import urllib.error
import urllib.request

import pytest
from websockets.sync.client import connect

from observer.events import parse_event
from observer.mock_script import CAST, CONSTRAINT_AT, NEWCOMER, RUN_LENGTH, prepare_timeline, run_timeline

SEED = "a coffee subscription"


def as_events(steps):
    return [{"run_id": "run_1", "ts": t, "type": type, "agent_id": agent, "payload": payload} for t, type, agent, payload in steps]


@pytest.fixture(scope="module")
def run():
    return run_timeline(SEED)


def test_same_seed_plays_out_the_same():
    assert run_timeline(SEED, 7) == run_timeline(SEED, 7)
    assert prepare_timeline(SEED) == prepare_timeline(SEED)
    assert run_timeline(SEED, 7) != run_timeline(SEED, 8)


@pytest.mark.parametrize("rng_seed", [1, 7, 42, 99])
def test_every_step_is_a_valid_event_in_time_order(rng_seed):
    steps = prepare_timeline(SEED) + run_timeline(SEED, rng_seed)
    for event in as_events(steps):
        parse_event(event)
    for timeline in (prepare_timeline(SEED), run_timeline(SEED, rng_seed)):
        times = [t for t, *_ in timeline]
        assert times == sorted(times)


def test_town_comes_online_one_building_at_a_time():
    steps = prepare_timeline(SEED)
    assert [type for _, type, _, _ in steps] == ["run.created"] + ["agent.born"] * 6 + ["run.ready"]
    born_times = [t for t, type, _, _ in steps if type == "agent.born"]
    assert all(0.6 <= b - a <= 0.8 for a, b in zip(born_times, born_times[1:]))
    assert steps[-1][3]["agent_ids"] == [c.persona["id"] for c in CAST]


def test_run_starts_and_ends(run):
    assert run[0][1] == "run.started"
    assert run[-1][:2] == (RUN_LENGTH, "run.ended")
    assert [s for s in run if s[1] == "world.event" and s[3]["kind"] == "constraint"][0][0] == CONSTRAINT_AT


@pytest.mark.parametrize("rng_seed", [1, 7, 42, 99])
def test_a_decision_every_two_to_four_seconds(rng_seed):
    times = [t for t, type, _, _ in run_timeline(SEED, rng_seed) if type == "agent.decision"]
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert len(times) > 40
    assert all(2 <= g <= 4 for g in gaps)


@pytest.mark.parametrize("rng_seed", [1, 7, 42, 99])
def test_one_death_and_a_replacement(rng_seed):
    run = run_timeline(SEED, rng_seed)
    died = [s for s in run if s[1] == "agent.died"]
    assert len(died) == 1
    death_t, _, dead, payload = died[0]
    assert 55 <= death_t <= 65
    assert len(payload["picked"]) == 3 and dead not in payload["picked"]
    assert [s for s in run if s[1] == "agent.last_words"][0][2] == dead

    # The dead character never acts again, and the newcomer only acts after it's born.
    assert not [s for s in run if s[2] == dead and s[0] > death_t and s[1] != "agent.died"]
    born = [s for s in run if s[1] == "agent.born"]
    assert len(born) == 1 and born[0][2] == NEWCOMER.persona["id"]
    assert born[0][3]["persona"]["lineage"] == {"generation": 1, "designed_by": payload["picked"], "replaces": dead}
    assert not [s for s in run if s[2] == born[0][2] and s[0] < born[0][0]]


@pytest.mark.parametrize("rng_seed", [1, 7, 42, 99])
def test_branches_and_endorsements_stay_consistent(rng_seed):
    run = run_timeline(SEED, rng_seed)
    branches, active = set(), {}
    for _, type, agent, payload in run:
        if type == "branch.created":
            assert all(p in branches for p in payload["parent_ids"])
            branches.add(payload["branch_id"])
        elif type == "endorse":
            assert payload["branch_id"] in branches
            active.setdefault(agent, set()).add(payload["branch_id"])
            assert len(active[agent]) <= 2
        elif type == "unendorse":
            active[agent].remove(payload["branch_id"])
        elif type == "agent.decision" and "target_branch" in payload:
            assert payload["target_branch"] in branches
    assert any(t == "branch.created" and p["op"] == "merge" for _, t, _, p in run)

    ended = run[-1][3]
    assert 1 <= len(ended["finalists"]) <= 3 and len(ended["baseline"]) == 3
    assert all(f["branch_id"] in branches for f in ended["finalists"])


def post(url, body=None):
    request = urllib.request.Request(url, data=json.dumps(body or {}).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def test_mock_world_plays_a_full_run_through_the_observer(start_server, start_observer):
    observer = start_observer()
    world = start_server("world", "observer.mock_world:app", {"OBSERVER_URL": observer, "MOCK_SPEED": "30"})

    with pytest.raises(urllib.error.HTTPError) as unknown:
        post(f"{world}/runs/run_nope/start")
    assert unknown.value.code == 404

    run_id = post(f"{world}/runs", {"seed": SEED})["run_id"]
    events = []
    with connect(observer.replace("http", "ws") + f"/ws/{run_id}") as ws:
        while not events or events[-1]["type"] != "run.ready":
            events.append(json.loads(ws.recv(timeout=10)))
        post(f"{world}/runs/{run_id}/start")
        with pytest.raises(urllib.error.HTTPError) as twice:
            post(f"{world}/runs/{run_id}/start")
        assert twice.value.code == 409
        started = time.monotonic()
        while events[-1]["type"] != "run.ended":
            events.append(json.loads(ws.recv(timeout=10)))
        elapsed = time.monotonic() - started

    for event in events:
        parse_event(event)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    types = [e["type"] for e in events]
    expected = [s[1] for s in prepare_timeline(SEED)] + [s[1] for s in run_timeline(SEED, 7)]
    assert types == expected
    assert RUN_LENGTH / 30 * 0.9 < elapsed < RUN_LENGTH / 30 + 3


@pytest.mark.parametrize("rng_seed", [1, 7, 42, 99])
def test_no_two_branches_say_the_same_thing(rng_seed):
    texts = [p["text"] for _, t, _, p in run_timeline(SEED, rng_seed) if t == "branch.created"]
    assert len(texts) == len(set(texts))
