"""The ideation voting loop end to end: the real world service, real
agents.character processes in ideation mode on the mock model, and the real
observer, on http+file and on SLIM plus the AGNTCY Directory.

The counts are the spec: A*A ideas in round 1, A*N in later rounds, ballots
of A*(A-1) and then N*(A-1) ideas, and N survivors every round. Between rounds
one agent dies and a designed replacement joins, never mid-round.
"""

import os
import uuid

import httpx
import pytest

from tests.test_world import BACKENDS, WorldProc, free_port, start_server, stop_server


@pytest.fixture
async def loop_world(request, tmp_path):
    observer_port, world_port = free_port(), free_port()
    observer_url = f"http://127.0.0.1:{observer_port}"
    env = {
        **os.environ,
        **request.param,
        "REGISTRY_DIR": str(tmp_path / "registry"),
        "ODDTOWN_TOWN": f"test-{uuid.uuid4().hex[:8]}",
        "AGENT_MODULE": "agents.character",
        "ODDTOWN_MODEL": "mock",
        "OBSERVER_URL": observer_url,
        "OBSERVER_DB": str(tmp_path / "observer.db"),
    }
    observer = await start_server(
        ["-m", "uvicorn", "observer.app:app", "--port", str(observer_port), "--log-level", "warning"],
        env, tmp_path / "observer.log", f"{observer_url}/runs",
    )
    world_proc = await start_server(
        ["-m", "world.service", "--port", str(world_port)],
        env, tmp_path / "world.log", f"http://127.0.0.1:{world_port}/docs",
    )
    w = WorldProc(f"http://127.0.0.1:{world_port}", observer_url)
    yield w
    try:
        if w.run_id and (await w.status()).get("state") not in ("ended", "failed"):
            await w.post("/stop")
    except httpx.TransportError:
        pass
    await stop_server(world_proc)
    await stop_server(observer)
    await w.client.aclose()
    await w.observer.aclose()


async def create(w, agents, rounds, top_n, **loop):
    response = await w.client.post("/runs", json={
        "seed": "a coffee subscription",
        "mode": "ideation_loop",
        "loop": {"agents": agents, "rounds": rounds, "top_n": top_n, "phase_timeout_s": 60, "random_seed": 7, **loop},
    })
    assert response.status_code == 200, response.text
    w.run_id = response.json()["run_id"]
    await w.wait_state("ready", timeout=60)


async def run_loop(w, agents, rounds, top_n, **loop):
    await create(w, agents, rounds, top_n, **loop)
    assert (await w.post("/start")).status_code == 200
    return await w.wait_state("ended", timeout=300)


def no_errors(events):
    errors = [e for e in events if e["type"] in ("agent.error", "llm.error", "agent.crashed")]
    assert not errors, errors


@pytest.mark.parametrize("loop_world", BACKENDS, indirect=True)
async def test_ideation_loop_counts_votes_survivors_and_replacements(loop_world):
    A, R, N = 4, 3, 3
    status = await run_loop(loop_world, A, R, N)
    assert status["result"]["reason"] == "ideation complete"
    assert status["mode"] == "ideation_loop"
    assert status["processes"] == []
    events = await loop_world.events()
    no_errors(events)

    started = {e["payload"]["round"]: e for e in events if e["type"] == "loop.round_started"}
    ended = [e["payload"] for e in events if e["type"] == "loop.round_ended"]
    assert [r["round"] for r in ended] == list(range(1, R + 1))
    cast = {r: set(started[r]["payload"]["participants"]) for r in started}
    assert all(len(cast[r]) == A for r in cast)  # always A agents

    everyone = set().union(*cast.values())
    ideas = {e["payload"]["branch_id"]: e for e in events if e["type"] == "branch.created" and e["agent_id"] in everyone}
    author = {i: e["agent_id"] for i, e in ideas.items()}
    by_round = {}
    for e in ideas.values():
        by_round.setdefault(e["payload"]["round"], []).append(e)

    # Round 1: A originals and A*(A-1) twists, nobody twisting their own.
    r1 = by_round[1]
    originals = [e for e in r1 if e["payload"]["parent_ids"] == ["br_seed"]]
    twists = [e for e in r1 if e["payload"]["parent_ids"] != ["br_seed"]]
    assert len(originals) == A and {e["agent_id"] for e in originals} == cast[1]
    assert len(twists) == A * (A - 1)
    assert all(author[e["payload"]["parent_ids"][0]] != e["agent_id"] for e in twists)
    assert ended[0]["pool_size"] == A * A

    # Later rounds: that round's A agents twist every survivor of the round before, newcomer included.
    for r in range(2, R + 1):
        survivors = {i["idea_id"] for i in ended[r - 2]["selected"]}
        new = by_round[r]
        assert len(new) == A * N
        assert {(e["agent_id"], e["payload"]["parent_ids"][0]) for e in new} == {(a, s) for a in cast[r] for s in survivors}
        assert ended[r - 1]["pool_size"] == A * N

    votes = [e for e in events if e["type"] == "loop.vote"]
    for r in range(1, R + 1):
        round_votes = [v for v in votes if v["payload"]["round"] == r]
        assert sorted(v["agent_id"] for v in round_votes) == sorted(cast[r])
        expected_ballot = A * (A - 1) if r == 1 else N * (A - 1)
        for v in round_votes:
            assert v["payload"]["ballot_size"] == expected_ballot
            assert author[v["payload"]["idea_id"]] != v["agent_id"]
        assert sum(ended[r - 1]["tally"].values()) == A
        assert ended[r - 1]["abstained"] == []
        assert len(ended[r - 1]["selected"]) == N

    # One random death and one designed birth after each round but the last.
    replaced = [e["payload"] for e in events if e["type"] == "loop.replaced"]
    assert [p["round"] for p in replaced] == list(range(1, R))
    for p in replaced:
        assert p["how"] == "random" and p["fallback"] is None and p["born"]
        r = p["round"]
        assert p["died"] in cast[r] and p["died"] not in cast[r + 1]
        assert p["born"] in cast[r + 1] and p["born"] not in cast[r]
        died = [e for e in events if e["type"] == "agent.died" and e["agent_id"] == p["died"]]
        assert len(died) == 1 and len(died[0]["payload"]["picked"]) == 3
        assert [e for e in events if e["type"] == "agent.last_words" and e["agent_id"] == p["died"]]
        born = [e for e in events if e["type"] == "agent.born" and e["agent_id"] == p["born"]][0]
        assert born["payload"]["requested_by"] in died[0]["payload"]["picked"]  # the last designer asked for it
        assert born["payload"]["persona"]["lineage"]["replaces"] == p["died"]
        steps = [e["payload"]["step"] for e in events if e["type"] == "replace.step" and e["payload"]["replaces"] == p["died"]]
        assert steps == ["propose", "refine", "refine", "birth"]

    # Nobody dies mid-round: every death sits between one round's end and the next round's start.
    seq = {e["seq"]: e for e in events}
    bounds = [(next(e["seq"] for e in events if e["type"] == "loop.round_ended" and e["payload"]["round"] == r),
               started[r + 1]["seq"]) for r in range(1, R)]
    for e in events:
        if e["type"] in ("agent.last_words", "agent.died"):
            assert any(lo < e["seq"] < hi for lo, hi in bounds), seq[e["seq"]]

    final = [e["payload"] for e in events if e["type"] == "run.ended"][0]
    assert [f["branch_id"] for f in final["finalists"]] == [i["idea_id"] for i in ended[-1]["selected"]]


@pytest.mark.parametrize("loop_world", BACKENDS[:1], indirect=True)
async def test_replacement_can_be_switched_off(loop_world):
    await run_loop(loop_world, 3, 3, 3, replace_each_round=False)
    events = await loop_world.events()
    no_errors(events)
    casts = [frozenset(e["payload"]["participants"]) for e in events if e["type"] == "loop.round_started"]
    assert len(casts) == 3 and len(set(casts)) == 1
    assert not [e for e in events if e["type"] in ("loop.replaced", "agent.died")]


@pytest.mark.parametrize("loop_world", BACKENDS, indirect=True)
async def test_a_crashed_agent_is_the_rounds_death(loop_world):
    """Kill one before round 1. Round 1 runs a member short until the deadline, then
    the crash, not a random draw, is what gets replaced."""
    await create(loop_world, 4, 3, 3, phase_timeout_s=3)
    victim = (await loop_world.status())["living"][0]
    assert (await loop_world.post(f"/agents/{victim}/kill")).json() == {"ok": True}
    assert (await loop_world.post("/start")).status_code == 200
    status = await loop_world.wait_state("ended", timeout=300)
    assert status["result"]["reason"] == "ideation complete"
    events = await loop_world.events()
    replaced = [e["payload"] for e in events if e["type"] == "loop.replaced"]
    assert replaced[0]["how"] == "crash" and replaced[0]["died"] == victim and replaced[0]["born"]
    assert replaced[1]["how"] == "random"
    died = [e for e in events if e["type"] == "agent.died" and e["agent_id"] == victim][0]
    assert died["payload"]["noticed_by"] and len(died["payload"]["picked"]) == 3
    casts = [e["payload"]["participants"] for e in events if e["type"] == "loop.round_started"]
    assert all(len(c) == 4 for c in casts) and victim not in casts[1]


@pytest.mark.parametrize(
    "loop_world", [pytest.param({**BACKENDS[0].values[0], "ODDTOWN_MOCK_REJECT_PERSONAS": "1"}, id="http+file")],
    indirect=True,
)
async def test_a_design_that_fails_its_checks_twice_falls_back_to_an_unused_persona(loop_world):
    """Every designed persona fails the content check. The last designer refines once more,
    that fails too, and the world starts an unused persona from the cast instead.
    Four agents, so three survivors make a full design team."""
    await run_loop(loop_world, 4, 3, 3)
    events = await loop_world.events()
    replaced = [e["payload"] for e in events if e["type"] == "loop.replaced"]
    assert [p["fallback"] for p in replaced] == ["design failed", "design failed"]
    for p in replaced:
        steps = [e["payload"]["step"] for e in events if e["type"] == "replace.step" and e["payload"]["replaces"] == p["died"]]
        assert steps == ["propose", "refine", "refine", "refine"]  # the last refine is the retry, and there's no birth
        born = [e for e in events if e["type"] == "agent.born" and e["agent_id"] == p["born"]][0]
        assert born["payload"]["requested_by"] == "world"
        assert born["payload"]["persona"]["lineage"]["replaces"] == p["died"]
    casts = [e["payload"]["participants"] for e in events if e["type"] == "loop.round_started"]
    assert all(len(c) == 4 for c in casts)
    assert (await loop_world.status())["result"]["reason"] == "ideation complete"


@pytest.mark.parametrize("loop_world", BACKENDS[:1], indirect=True)
async def test_a_slow_design_still_never_adds_an_extra_agent(loop_world):
    """With almost no time to design, the loop either waits for a newcomer that's already
    starting or falls back, but every round still has exactly A agents."""
    await run_loop(loop_world, 3, 3, 3, replace_timeout_s=0.05)
    events = await loop_world.events()
    assert len([e for e in events if e["type"] == "loop.replaced"]) == 2
    casts = [e["payload"]["participants"] for e in events if e["type"] == "loop.round_started"]
    assert all(len(c) == 3 for c in casts)
    born_after_start = [e for e in events if e["type"] == "agent.born" and e["payload"].get("requested_by")]
    assert len(born_after_start) == 2  # one newcomer per boundary, never a second


@pytest.mark.parametrize("loop_world", BACKENDS[:1], indirect=True)
async def test_loop_settings_are_bounded(loop_world):
    for loop in ({"agents": 2}, {"agents": 13}, {"rounds": 2}, {"rounds": 11}, {"top_n": 2}, {"top_n": 7}):
        response = await loop_world.client.post("/runs", json={"seed": "x", "mode": "ideation_loop", "loop": loop})
        assert response.status_code == 422, loop
    response = await loop_world.client.post("/runs", json={"seed": "x", "mode": "brainstorm"})
    assert response.status_code == 422
