"""The ideation voting loop end to end: the real world service, real
agents.character processes in ideation mode on the mock model, and the real
observer, on http+file and on SLIM plus the AGNTCY Directory.

The counts are the spec: A*A ideas in round 1, A*N in later rounds, ballots
of A*(A-1) and then N*(A-1) ideas, and N survivors every round.
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


async def run_loop(w, agents, rounds, top_n):
    response = await w.client.post("/runs", json={
        "seed": "a coffee subscription",
        "mode": "ideation_loop",
        "loop": {"agents": agents, "rounds": rounds, "top_n": top_n, "phase_timeout_s": 60, "random_seed": 7},
    })
    assert response.status_code == 200, response.text
    w.run_id = response.json()["run_id"]
    await w.wait_state("ready", timeout=60)
    assert (await w.post("/start")).status_code == 200
    return await w.wait_state("ended", timeout=240)


@pytest.mark.parametrize("loop_world", BACKENDS, indirect=True)
async def test_ideation_loop_counts_votes_and_survivors(loop_world):
    A, R, N = 4, 3, 3
    status = await run_loop(loop_world, A, R, N)
    assert status["result"]["reason"] == "ideation complete"
    assert status["mode"] == "ideation_loop"
    assert status["processes"] == []
    events = await loop_world.events()
    agents = {e["agent_id"] for e in events if e["type"] == "agent.born"}
    assert len(agents) == A

    errors = [e for e in events if e["type"] in ("agent.error", "llm.error", "agent.crashed")]
    assert not errors, errors

    ideas = {e["payload"]["branch_id"]: e for e in events if e["type"] == "branch.created" and e["agent_id"] in agents}
    author = {i: e["agent_id"] for i, e in ideas.items()}
    by_round = {}
    for e in ideas.values():
        by_round.setdefault(e["payload"]["round"], []).append(e)
    assert sorted(by_round) == list(range(1, R + 1))

    ended = [e["payload"] for e in events if e["type"] == "loop.round_ended"]
    assert [r["round"] for r in ended] == list(range(1, R + 1))

    # Round 1: A originals of the seed and A*(A-1) twists, nobody twisting their own.
    r1 = by_round[1]
    originals = [e for e in r1 if e["payload"]["parent_ids"] == ["br_seed"]]
    twists = [e for e in r1 if e["payload"]["parent_ids"] != ["br_seed"]]
    assert len(originals) == A and {e["agent_id"] for e in originals} == agents
    assert len(twists) == A * (A - 1)
    assert all(author[e["payload"]["parent_ids"][0]] != e["agent_id"] for e in twists)
    assert ended[0]["pool_size"] == A * A

    # Later rounds: every agent twists every survivor of the round before.
    for r in range(2, R + 1):
        survivors = {i["idea_id"] for i in ended[r - 2]["selected"]}
        new = by_round[r]
        assert len(new) == A * N
        assert {(e["agent_id"], e["payload"]["parent_ids"][0]) for e in new} == {(a, s) for a in agents for s in survivors}
        assert ended[r - 1]["pool_size"] == A * N

    # One vote each per round, never for an idea the voter last edited, over a ballot of the right size.
    votes = [e for e in events if e["type"] == "loop.vote"]
    for r in range(1, R + 1):
        round_votes = [v for v in votes if v["payload"]["round"] == r]
        assert sorted(v["agent_id"] for v in round_votes) == sorted(agents)
        expected_ballot = A * (A - 1) if r == 1 else N * (A - 1)
        for v in round_votes:
            assert v["payload"]["ballot_size"] == expected_ballot
            assert author[v["payload"]["idea_id"]] != v["agent_id"]
        assert sum(ended[r - 1]["tally"].values()) == A
        assert ended[r - 1]["abstained"] == []
        assert len(ended[r - 1]["selected"]) == N

    # The town's answer is the last round's survivors.
    final = [e["payload"] for e in events if e["type"] == "run.ended"][0]
    assert [f["branch_id"] for f in final["finalists"]] == [i["idea_id"] for i in ended[-1]["selected"]]
    assert all(f["champions"] == [author[f["branch_id"]]] for f in final["finalists"])


@pytest.mark.parametrize("loop_world", BACKENDS[:1], indirect=True)
async def test_loop_settings_are_bounded(loop_world):
    for loop in ({"agents": 2}, {"agents": 13}, {"rounds": 2}, {"rounds": 11}, {"top_n": 2}, {"top_n": 7}):
        response = await loop_world.client.post("/runs", json={"seed": "x", "mode": "ideation_loop", "loop": loop})
        assert response.status_code == 422, loop
    response = await loop_world.client.post("/runs", json={"seed": "x", "mode": "brainstorm"})
    assert response.status_code == 422
