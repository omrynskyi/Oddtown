"""The real town end to end: Oleg's observer, Clara's world service, and six real
agents.character processes on http+file, using the mock model so it needs no API key.

Lifespans are a few actions long, so characters die, survivors design their
replacements, and newcomers join. Every event checked here went through
observer.client, so it passed the observer's contract.
"""

import asyncio
import os
import socket
import sys
import uuid

import httpx
import pytest

from agents.tests.test_units import CAST, ROOT


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def start_server(args, env, log_path, health_url):
    proc = await asyncio.create_subprocess_exec(
        sys.executable, *args, cwd=ROOT, env=env, stdout=asyncio.subprocess.DEVNULL, stderr=open(log_path, "wb"),
    )
    async with httpx.AsyncClient() as client:
        for _ in range(150):
            try:
                await client.get(health_url)
                return proc
            except httpx.TransportError:
                if proc.returncode is not None:
                    raise RuntimeError(f"{args} exited, see {log_path}")
                await asyncio.sleep(0.2)
    raise RuntimeError(f"{args} never came up, see {log_path}")


async def stop_server(proc):
    if proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 15)
        except asyncio.TimeoutError:
            proc.kill()


@pytest.fixture
async def town(tmp_path):
    observer_port, world_port = free_port(), free_port()
    observer_url = f"http://127.0.0.1:{observer_port}"
    env = {
        **os.environ,
        "TRANSPORT": "http",
        "REGISTRY": "file",
        "REGISTRY_DIR": str(tmp_path / "registry"),
        "ODDTOWN_TOWN": f"test-{uuid.uuid4().hex[:8]}",
        "AGENT_MODULE": "agents.character",
        "OBSERVER_URL": observer_url,
        "OBSERVER_DB": str(tmp_path / "observer.db"),
        "ODDTOWN_MODEL": "mock",
        "ODDTOWN_TIME_SCALE": "0.05",  # 3 s batch window -> 0.15 s, 20-40 s idle -> 1-2 s
        "ODDTOWN_MAX_ACTIONS": "10",  # staggered to 5-10 actions, so deaths come within seconds
        "ODDTOWN_REST_AFTER_S": "2000",  # keep the town talking until the test stops it
    }
    observer = await start_server(
        ["-m", "uvicorn", "observer.app:app", "--port", str(observer_port), "--log-level", "warning"],
        env, tmp_path / "observer.log", f"{observer_url}/runs",
    )
    world = await start_server(
        ["-m", "world.service", "--port", str(world_port)], env, tmp_path / "world.log", f"http://127.0.0.1:{world_port}/docs",
    )
    client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{world_port}", timeout=60)
    obs = httpx.AsyncClient(base_url=observer_url, timeout=30)
    state = {"run_id": None, "client": client, "obs": obs, "tmp": tmp_path}
    yield state
    if state["run_id"]:
        try:
            await client.post(f"/runs/{state['run_id']}/stop")
        except httpx.TransportError:
            pass
    await stop_server(world)
    await stop_server(observer)
    await client.aclose()
    await obs.aclose()


async def events(town, type=None):
    found = (await town["obs"].get(f"/runs/{town['run_id']}/events")).json()
    return [e for e in found if type is None or e["type"] == type]


async def until(town, predicate, timeout=60):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        all_events = await events(town)
        if predicate(all_events):
            return all_events
        if loop.time() > deadline:
            types = {}
            for e in all_events:
                types[e["type"]] = types.get(e["type"], 0) + 1
            raise AssertionError(f"timed out; events so far: {types}; logs in {town['tmp']}")
        await asyncio.sleep(0.3)


async def test_six_characters_gossip_die_and_get_replaced(town):
    client = town["client"]
    response = await client.post("/runs", json={"seed": "a coffee subscription", "cast": CAST, "time_budget_s": 120})
    assert response.status_code == 200, response.text
    town["run_id"] = run_id = response.json()["run_id"]
    await until(town, lambda es: any(e["type"] == "run.ready" for e in es), timeout=60)
    assert (await client.post(f"/runs/{run_id}/start")).status_code == 200

    def replaced(es):
        return any(e["type"] == "agent.born" and e["payload"].get("requested_by") for e in es)

    all_events = await until(town, replaced, timeout=90)
    cast_ids = {p["id"] for p in CAST}

    # Gossip: real decisions, branches, endorsements and messages from the characters themselves.
    decisions = [e for e in all_events if e["type"] == "agent.decision"]
    assert len({e["agent_id"] for e in decisions} & cast_ids) >= 5
    assert all(len(e["payload"]["say"]) <= 100 for e in decisions)
    branches = [e for e in all_events if e["type"] == "branch.created"]
    assert any(e["payload"]["op"] == "twist" and e["agent_id"] in cast_ids for e in branches)
    assert any(e["type"] == "idea.share" for e in all_events)

    # Every LLM call was counted, for the observer and for the world's token budget.
    calls = [e for e in all_events if e["type"] == "llm.call"]
    assert calls and all(e["payload"]["input_tokens"] > 0 and e["payload"]["sections"] for e in calls)
    status = (await client.get(f"/runs/{run_id}")).json()
    assert status["tokens"] > 0

    # A death: last words, then agent.died with the three picked designers.
    died = [e for e in all_events if e["type"] == "agent.died" and e["agent_id"] in cast_ids]
    assert died, "nobody died"
    assert any(len(e["payload"]["picked"]) == 3 for e in died)
    last_words = {e["agent_id"] for e in all_events if e["type"] == "agent.last_words"}
    assert {e["agent_id"] for e in died if "noticed_by" not in e["payload"]} <= last_words

    # A replacement: propose, refine, refine, then the world starts the newcomer.
    steps = [e["payload"]["step"] for e in all_events if e["type"] == "replace.step"]
    assert "propose" in steps and "refine" in steps and "birth" in steps
    newcomer = next(e for e in all_events if e["type"] == "agent.born" and e["payload"].get("requested_by"))
    persona = newcomer["payload"]["persona"]
    assert persona["lineage"]["generation"] == 1 and persona["lineage"]["replaces"] in cast_ids
    assert len(persona["lineage"]["designed_by"]) == 3

    # The newcomer joins the gossip with the seed from its briefing.
    await until(town, lambda es: any(e["type"] == "agent.decision" and e["agent_id"] == persona["id"] for e in es), timeout=60)

    # Stopping ends the run with finalists once some living character backs a branch.
    async def living_support():
        st = (await client.get(f"/runs/{run_id}")).json()
        return any(st["endorsements"].get(a) for a in st["living"])

    for _ in range(200):
        if await living_support():
            break
        await asyncio.sleep(0.3)
    result = (await client.post(f"/runs/{run_id}/stop")).json()
    town["run_id"] = None
    assert result["finalists"]
    all_events = await events({**town, "run_id": run_id})
    assert any(e["type"] == "run.ended" for e in all_events)
    errors = [e for e in all_events if e["type"] in ("agent.error", "llm.error", "agent.crashed")]
    assert not errors, errors


async def test_a_killed_character_is_noticed_and_replaced_by_a_peer(town):
    """The crash demo: SIGKILL a character, a peer's message bounces, and that peer does the pick."""
    client = town["client"]
    response = await client.post("/runs", json={"seed": "a coffee subscription", "cast": CAST, "time_budget_s": 120})
    town["run_id"] = run_id = response.json()["run_id"]
    await until(town, lambda es: any(e["type"] == "run.ready" for e in es), timeout=60)
    await client.post(f"/runs/{run_id}/start")
    await until(town, lambda es: sum(e["type"] == "agent.decision" for e in es) >= 3, timeout=30)

    status = (await client.get(f"/runs/{run_id}")).json()
    victim = next(a for a in status["processes"] if a in status["living"])
    assert (await client.post(f"/runs/{run_id}/agents/{victim}/kill")).status_code == 200

    def noticed(es):
        return any(e["type"] == "agent.died" and e["agent_id"] == victim and e["payload"].get("noticed_by") for e in es)

    def replaced(es):
        return any(e["type"] == "agent.born" and e["payload"]["persona"]["lineage"]["replaces"] == victim for e in es)

    all_events = await until(town, lambda es: noticed(es) and replaced(es), timeout=90)
    died = next(e for e in all_events if e["type"] == "agent.died" and e["agent_id"] == victim)
    assert died["payload"]["noticed_by"] != victim and 1 <= len(died["payload"]["picked"]) <= 3
    assert not any(e["type"] == "agent.last_words" and e["agent_id"] == victim for e in all_events)
