"""Steps 5, 6 and 7 end to end: a real world service process launching real
character processes (tests.fake_character), on http+file and on SLIM plus the
AGNTCY Directory.
"""

import asyncio
import json
import os
import socket
import sys
import uuid

import httpx
import pytest

from tests.procs import ROOT, wait_for

BACKENDS = [
    pytest.param({"TRANSPORT": "http", "REGISTRY": "file"}, id="http+file"),
    pytest.param({"TRANSPORT": "slim", "REGISTRY": "agntcy"}, id="slim+agntcy", marks=pytest.mark.infra),
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class WorldProc:
    def __init__(self, proc, url, log_path):
        self.proc = proc
        self.client = httpx.AsyncClient(base_url=url, timeout=120)
        self.log_path = log_path

    def events(self, type=None, agent_id=None):
        if not self.log_path.exists():
            return []
        events = [json.loads(line) for line in self.log_path.read_text().splitlines() if line.strip()]
        return [
            e for e in events
            if (type is None or e["type"] == type) and (agent_id is None or e["agent_id"] == agent_id)
        ]

    async def status(self):
        return (await self.client.get("/run")).json()

    async def wait_state(self, state, timeout=60):
        async def poll():
            while (await self.status())["state"] != state:
                await asyncio.sleep(0.2)

        await asyncio.wait_for(poll(), timeout)
        return await self.status()


@pytest.fixture
async def world(request, tmp_path):
    port = free_port()
    log_path = tmp_path / "events.jsonl"
    env = {
        **os.environ,
        **request.param,
        "REGISTRY_DIR": str(tmp_path / "registry"),
        "ODDTOWN_TOWN": f"test-{uuid.uuid4().hex[:8]}",
        "AGENT_MODULE": "tests.fake_character",
        "OBSERVER_LOG": str(log_path),
    }
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "world.service", "--port", str(port),
        cwd=ROOT, env=env, stdout=asyncio.subprocess.DEVNULL, stderr=open(tmp_path / "world.log", "wb"),
    )
    w = WorldProc(proc, f"http://127.0.0.1:{port}", log_path)

    async def up():
        while True:
            try:
                await w.client.get("/run")
                return
            except httpx.TransportError:
                await asyncio.sleep(0.2)

    await asyncio.wait_for(up(), 30)
    yield w
    errors = w.events("test.error")
    try:
        if (await w.status()).get("state") not in ("idle", "ended"):
            await w.client.post("/run/stop")
    except httpx.TransportError:
        pass
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), 15)
    except asyncio.TimeoutError:
        proc.kill()
    await w.client.aclose()
    assert not errors, f"a character hit an error: {errors}"


def persona(agent_id, **script):
    return {"id": agent_id, "name": agent_id.title(), "script": script}


async def start(world, cast, **kwargs):
    response = await world.client.post("/run", json={"seed": "a coffee subscription", "cast": cast, **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


def seeds_received(world):
    return {
        e["agent_id"] for e in world.events("test.received")
        if e["payload"]["type"] == "world.event" and e["payload"]["body"]["kind"] == "seed"
    }


# -- step 5: world service and launcher ---------------------------------------


@pytest.mark.parametrize("world", BACKENDS, indirect=True)
async def test_six_characters_launch_get_the_seed_and_rest(world):
    cast = [persona(f"char_{i}", rest_after=0.5) for i in range(6)]
    started = await start(world, cast)
    assert started["agents"] == [p["id"] for p in cast]
    status = await world.wait_state("ended")
    assert status["result"]["reason"] == "town at rest"
    assert seeds_received(world) == {p["id"] for p in cast}
    # A hello reaches whoever had already joined, so in every pair the later one was heard.
    heard = {
        (e["agent_id"], e["payload"]["from"]) for e in world.events("test.received")
        if e["payload"]["type"] == "agent.hello"
    }
    ids = [p["id"] for p in cast]
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            assert (a, b) in heard or (b, a) in heard
    assert status["processes"] == []
    assert status["living"] == []


@pytest.mark.parametrize("world", BACKENDS, indirect=True)
async def test_time_budget_ends_the_run(world):
    await start(world, [persona("char_a"), persona("char_b")], time_budget_s=2)
    status = await world.wait_state("ended", timeout=30)
    assert status["result"]["reason"] == "time budget"
    assert status["processes"] == []


@pytest.mark.parametrize("world", BACKENDS, indirect=True)
async def test_token_budget_ends_the_run(world):
    await start(world, [persona(f"char_{i}", tokens=1000) for i in range(4)], token_budget=3500)
    status = await world.wait_state("ended", timeout=30)
    assert status["result"]["reason"] == "token budget"
    assert status["tokens"] == 4000


@pytest.mark.parametrize("world", BACKENDS, indirect=True)
async def test_inject_reaches_everyone_and_runs_can_follow_each_other(world):
    await start(world, [persona("char_a"), persona("char_b"), persona("char_c")])
    response = await world.client.post("/run/inject", json={"kind": "constraint", "text": "no electricity"})
    assert response.json() == {"ok": True, "bounced": []}

    def got_constraint():
        return {
            e["agent_id"] for e in world.events("test.received")
            if e["payload"]["type"] == "world.event" and e["payload"]["body"].get("text") == "no electricity"
        }

    await wait_for(lambda: got_constraint() == {"char_a", "char_b", "char_c"})
    assert (await world.client.post("/run")).status_code == 422  # missing seed
    assert (await world.client.post("/run", json={"seed": "x", "cast": [persona("x")]})).status_code == 409
    result = (await world.client.post("/run/stop")).json()
    assert result["reason"] == "stopped by hand"

    second = await start(world, [persona("char_d", rest_after=0.2), persona("char_e", rest_after=0.2)])
    assert second["agents"] == ["char_d", "char_e"]
    status = await world.wait_state("ended")
    assert status["result"]["reason"] == "town at rest"


# -- step 6: births, deaths and crashes ----------------------------------------


@pytest.mark.parametrize("world", BACKENDS, indirect=True)
async def test_death_then_newcomer_is_born_and_joins(world):
    newcomer = persona("char_new", endorse=[])
    cast = [persona("char_a", die_after=0.5, spawn=newcomer), persona("char_b"), persona("char_c")]
    await start(world, cast)

    async def living():
        return (await world.status())["living"]

    async def wait_living(expected):
        while sorted(await living()) != expected:
            await asyncio.sleep(0.2)

    await asyncio.wait_for(wait_living(["char_b", "char_c", "char_new"]), 30)
    assert world.events("agent.died", agent_id="char_a")
    spawned = world.events("agent.spawned")
    assert spawned and spawned[0]["payload"]["requested_by"] == "char_a"
    # the newcomer's hello reached the survivors
    await wait_for(
        lambda: {
            e["agent_id"] for e in world.events("test.received")
            if e["payload"]["type"] == "agent.hello" and e["payload"]["from"] == "char_new"
        } >= {"char_b", "char_c"},
        timeout=20,
    )
    exited = world.events("agent.exited", agent_id="char_a")
    assert exited and exited[0]["payload"]["exit_code"] == 0
    assert (await world.status())["dead"] == ["char_a"]


@pytest.mark.parametrize("world", BACKENDS, indirect=True)
async def test_spawn_with_a_bad_id_is_refused(world):
    bad = {"id": "../../escape", "name": "Bad", "script": {}}
    await start(world, [persona("char_a", die_after=0.3, spawn=bad), persona("char_b")])
    await wait_for(lambda: world.events("agent.died", agent_id="char_a"), timeout=20)
    await asyncio.sleep(1)
    assert world.events("agent.spawned") == []
    assert (await world.status())["living"] == ["char_b"]


@pytest.mark.parametrize("world", BACKENDS, indirect=True)
async def test_killed_character_is_noticed_by_the_peer_that_bounces(world):
    await start(world, [persona("char_a", send_to=["char_b", 3]), persona("char_b"), persona("char_c")])
    assert (await world.client.post("/run/agents/char_b/kill")).json() == {"ok": True}
    await wait_for(lambda: world.events("agent.crashed", agent_id="char_b"), timeout=10)
    # nobody removed char_b's record yet, until char_a's send bounces
    await wait_for(lambda: world.events("test.sent", agent_id="char_a"), timeout=30)
    assert world.events("test.sent", agent_id="char_a")[0]["payload"] == {"to": "char_b", "ok": False}
    assert (await world.status())["living"] == ["char_a", "char_c"]
    assert (await world.client.post("/run/agents/char_b/kill")).status_code == 404


# -- step 7: run end -----------------------------------------------------------


@pytest.mark.parametrize("world", BACKENDS, indirect=True)
async def test_run_ends_with_finalists_by_living_support(world):
    cast = [
        persona("char_a", endorse=["br_oak"], reason="it lasts", rest_after=1.5),
        persona("char_b", endorse=["br_oak", "br_pigeon"], reason="ritual", rest_after=1.5),
        persona("char_c", endorse=["br_pigeon"], object_to=["br_oak", "needs a delivery plan"], rest_after=1.5),
        persona("char_d", endorse=["br_reef"], rest_after=1.5),
        persona("char_e", endorse=["br_oak"], die_after=0.3),
    ]
    await start(world, cast)
    status = await world.wait_state("ended")
    result = status["result"]
    assert result["reason"] == "town at rest"
    assert [(f["branch_id"], f["support"]) for f in result["finalists"]] == [
        ("br_oak", 2),  # char_e died, so its endorsement no longer counts
        ("br_pigeon", 2),
        ("br_reef", 1),
    ]
    oak = result["finalists"][0]
    assert oak["branch"]["text"] == "br_oak on a coffee subscription"
    assert {c["agent_id"] for c in oak["champions"]} == {"char_a", "char_b"}
    assert oak["minority_report"] == {"agent_id": "char_c", "reason": "needs a delivery plan"}
    assert result["ties"] == []
    ended = world.events("run.ended")
    assert len(ended) == 1 and ended[0]["payload"]["finalists"] == result["finalists"]


@pytest.mark.parametrize("world", BACKENDS, indirect=True)
async def test_tie_at_the_cut_asks_a_human(world):
    cast = [persona(f"char_{i}", endorse=[f"br_{i}"]) for i in range(4)]
    await start(world, cast, time_budget_s=3)
    status = await world.wait_state("ended", timeout=30)
    assert status["result"]["ties"] == [["br_0", "br_1", "br_2", "br_3"]]
    requests = world.events("human.request")
    assert requests and requests[0]["payload"]["kind"] == "tie"
    request_id = requests[0]["payload"]["request_id"]
    assert (await world.client.post(f"/run/human/{request_id}/answer", json={"answer": "keep both"})).json() == {"ok": True}
    assert world.events("human.answer")[0]["payload"] == {"request_id": request_id, "answer": "keep both"}
