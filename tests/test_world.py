"""Steps 5, 6 and 7 end to end: a real world service process launching real
character processes (tests.fake_character), reporting to Oleg's real observer,
on http+file and on SLIM plus the AGNTCY Directory.

Every event the tests look at went through observer.client and the observer's
contract check, so a world event that breaks the contract never shows up and
the test that wants it fails.
"""

import asyncio
import os
import socket
import sys
import uuid

import httpx
import pytest

from tests.procs import ROOT

BACKENDS = [
    pytest.param({"TRANSPORT": "http", "REGISTRY": "file"}, id="http+file"),
    pytest.param({"TRANSPORT": "slim", "REGISTRY": "agntcy"}, id="slim+agntcy", marks=pytest.mark.infra),
]

TRAITS = {"time_horizon": 0.1, "tech_attitude": 0.2, "scale": 0.3, "sociality": 0.4, "risk": 0.5, "rationality": 0.6}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def start_server(args, env, log_path, health_url):
    proc = await asyncio.create_subprocess_exec(
        sys.executable, *args, cwd=ROOT, env=env,
        stdout=asyncio.subprocess.DEVNULL, stderr=open(log_path, "wb"),
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


class WorldProc:
    def __init__(self, world_url, observer_url):
        self.client = httpx.AsyncClient(base_url=world_url, timeout=120)
        self.observer = httpx.AsyncClient(base_url=observer_url, timeout=30)
        self.run_id: str | None = None

    async def events(self, type=None, agent_id=None):
        if not self.run_id:
            return []
        events = (await self.observer.get(f"/runs/{self.run_id}/events")).json()
        return [
            e for e in events
            if (type is None or e["type"] == type) and (agent_id is None or e["agent_id"] == agent_id)
        ]

    async def until(self, predicate, timeout=30):
        """Wait until predicate(all events of the run) is truthy, and return it."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            result = predicate(await self.events())
            if result:
                return result
            if loop.time() > deadline:
                raise AssertionError("timed out waiting for events")
            await asyncio.sleep(0.2)

    async def status(self):
        return (await self.client.get(f"/runs/{self.run_id}")).json()

    async def wait_state(self, state, timeout=60):
        async def poll():
            while (await self.status())["state"] != state:
                await asyncio.sleep(0.2)

        await asyncio.wait_for(poll(), timeout)
        return await self.status()

    async def post(self, path, **kwargs):
        return await self.client.post(f"/runs/{self.run_id}{path}", **kwargs)


@pytest.fixture
async def world(request, tmp_path):
    observer_port, world_port = free_port(), free_port()
    observer_url = f"http://127.0.0.1:{observer_port}"
    env = {
        **os.environ,
        **request.param,
        "REGISTRY_DIR": str(tmp_path / "registry"),
        "ODDTOWN_TOWN": f"test-{uuid.uuid4().hex[:8]}",
        "AGENT_MODULE": "tests.fake_character",
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
    errors = await w.events("test.error")
    try:
        if w.run_id and (await w.status()).get("state") not in ("ended", "failed"):
            await w.post("/stop")
    except httpx.TransportError:
        pass
    await stop_server(world_proc)
    await stop_server(observer)
    await w.client.aclose()
    await w.observer.aclose()
    assert not errors, f"a character hit an error: {errors}"


def persona(agent_id, **script):
    return {"id": agent_id, "name": agent_id.title(), "one_liner": f"{agent_id} for tests", "traits": TRAITS,
            "script": script}


async def start(world, cast, **kwargs):
    """The UI's flow: create the run, wait for run.ready, then start it."""
    response = await world.client.post("/runs", json={"seed": "a coffee subscription", "cast": cast, **kwargs})
    assert response.status_code == 200, response.text
    world.run_id = response.json()["run_id"]
    await world.wait_state("ready", timeout=60)
    response = await world.post("/start")
    assert response.status_code == 200, response.text


def received(events, msg_type=None, **body):
    """test.received events whose envelope has this type and these body fields."""
    found = []
    for e in events:
        if e["type"] != "test.received":
            continue
        envelope = e["payload"]
        if msg_type and envelope["type"] != msg_type:
            continue
        if all((envelope.get("body") or {}).get(k) == v for k, v in body.items()):
            found.append(e)
    return found


# -- step 5: world service and launcher ---------------------------------------


@pytest.mark.parametrize("world", BACKENDS, indirect=True)
async def test_six_characters_launch_get_the_seed_and_rest(world):
    cast = [persona(f"char_{i}", rest_after=0.5) for i in range(6)]
    await start(world, cast)
    status = await world.wait_state("ended")
    assert status["result"]["reason"] == "town at rest"
    events = await world.events()
    ids = [p["id"] for p in cast]

    # The observer saw the UI's sequence, in order, and every payload passed its contract.
    types = [e["type"] for e in events if e["agent_id"] is None or e["type"] == "agent.born"]
    assert types[0] == "run.created"
    assert types.index("run.ready") > max(i for i, t in enumerate(types) if t == "agent.born")
    assert types.index("run.started") > types.index("run.ready")
    assert {e["agent_id"] for e in events if e["type"] == "agent.born"} == set(ids)
    assert [e["payload"] for e in events if e["type"] == "run.ready"] == [{"agent_ids": ids}]
    assert [e["payload"] for e in events if e["type"] == "world.event"] == [
        {"kind": "seed", "text": "a coffee subscription"}
    ]
    assert {e["agent_id"] for e in received(events, "world.event", kind="seed")} == set(ids)
    seed = received(events, "world.event", kind="seed")[0]["payload"]["body"]
    assert seed["branch"]["branch_id"] == "br_seed"

    # A hello reaches whoever had already joined, so in every pair the later one was heard.
    heard = {(e["agent_id"], e["payload"]["from"]) for e in received(events, "agent.hello")}
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
    response = await world.post("/inject", json={"kind": "constraint", "text": "no electricity"})
    assert response.json() == {"ok": True, "bounced": []}
    await world.until(
        lambda ev: {e["agent_id"] for e in received(ev, "world.event", text="no electricity")}
        == {"char_a", "char_b", "char_c"}
    )
    assert {"kind": "constraint", "text": "no electricity"} in [e["payload"] for e in await world.events("world.event")]
    assert (await world.post("/inject", json={"kind": "stop", "text": "x"})).status_code == 422
    assert (await world.client.post("/runs", json={})).status_code == 422  # missing seed
    assert (await world.client.post("/runs", json={"seed": "x", "cast": [persona("x")]})).status_code == 409
    assert (await world.client.post("/runs/run_nope/start")).status_code == 404
    result = (await world.post("/stop")).json()
    assert result["reason"] == "stopped by hand"

    await start(world, [persona("char_d", rest_after=0.2), persona("char_e", rest_after=0.2)])
    status = await world.wait_state("ended")
    assert status["result"]["reason"] == "town at rest"


# -- step 6: births, deaths and crashes ----------------------------------------


@pytest.mark.parametrize("world", BACKENDS, indirect=True)
async def test_death_then_newcomer_is_born_and_joins(world):
    newcomer = persona("char_new", endorse=[])
    cast = [persona("char_a", die_after=0.5, spawn=newcomer), persona("char_b"), persona("char_c")]
    await start(world, cast)

    async def wait_living(expected):
        while (await world.status())["living"] != expected:
            await asyncio.sleep(0.2)

    await asyncio.wait_for(wait_living(["char_b", "char_c", "char_new"]), 30)
    born = await world.until(lambda ev: [e for e in ev if e["type"] == "agent.born" and e["agent_id"] == "char_new"])
    assert born[0]["payload"]["requested_by"] == "char_a"
    assert born[0]["payload"]["persona"]["id"] == "char_new"
    # the newcomer's hello reached the survivors
    await world.until(
        lambda ev: {e["agent_id"] for e in received(ev, "agent.hello") if e["payload"]["from"] == "char_new"}
        >= {"char_b", "char_c"}
    )
    exited = await world.until(lambda ev: [e for e in ev if e["type"] == "agent.exited" and e["agent_id"] == "char_a"])
    assert exited[0]["payload"]["exit_code"] == 0
    assert (await world.status())["dead"] == ["char_a"]


@pytest.mark.parametrize("world", BACKENDS, indirect=True)
async def test_spawn_with_a_bad_id_is_refused(world):
    bad = {"id": "../../escape", "name": "Bad", "script": {}}
    await start(world, [persona("char_a", die_after=0.3, spawn=bad), persona("char_b")])
    await world.until(lambda ev: [e for e in ev if e["type"] == "agent.exited" and e["agent_id"] == "char_a"])
    await asyncio.sleep(1)
    assert [e["agent_id"] for e in await world.events("agent.born")] == ["char_a", "char_b"]
    assert (await world.status())["living"] == ["char_b"]


@pytest.mark.parametrize("world", BACKENDS, indirect=True)
async def test_killed_character_is_noticed_by_the_peer_that_bounces(world):
    await start(world, [persona("char_a", send_to=["char_b", 3]), persona("char_b"), persona("char_c")])
    assert (await world.post("/agents/char_b/kill")).json() == {"ok": True}
    await world.until(lambda ev: [e for e in ev if e["type"] == "agent.crashed" and e["agent_id"] == "char_b"])
    # nobody removed char_b's record yet, until char_a's send bounces
    sent = await world.until(lambda ev: [e for e in ev if e["type"] == "test.sent" and e["agent_id"] == "char_a"])
    assert sent[0]["payload"] == {"to": "char_b", "ok": False}
    assert (await world.status())["living"] == ["char_a", "char_c"]
    assert (await world.post("/agents/char_b/kill")).status_code == 404


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
    assert result["ties"] == []

    # The observer got it in the shape the UI reads.
    ended = await world.until(lambda ev: [e for e in ev if e["type"] == "run.ended"])
    assert len(ended) == 1
    payload = ended[0]["payload"]
    assert payload["baseline"] == []
    oak = payload["finalists"][0]
    assert oak["branch_id"] == "br_oak"
    assert oak["text"] == "br_oak on a coffee subscription"
    assert sorted(oak["champions"]) == ["char_a", "char_b"]
    assert oak["minority_report"] == "needs a delivery plan"
    assert oak["reasons"] == {"char_a": "it lasts", "char_b": "ritual"}


@pytest.mark.parametrize("world", BACKENDS, indirect=True)
async def test_tie_at_the_cut_asks_a_human(world):
    cast = [persona(f"char_{i}", endorse=[f"br_{i}"]) for i in range(4)]
    await start(world, cast, time_budget_s=3)
    status = await world.wait_state("ended", timeout=30)
    assert status["result"]["ties"] == [["br_0", "br_1", "br_2", "br_3"]]
    requests = await world.until(lambda ev: [e for e in ev if e["type"] == "human.request"])
    assert requests[0]["payload"]["kind"] == "tie"
    request_id = requests[0]["payload"]["request_id"]
    response = await world.post(f"/human/{request_id}/answer", json={"answer": "keep both"})
    assert response.json() == {"ok": True}
    answers = await world.until(lambda ev: [e for e in ev if e["type"] == "human.answer"])
    assert answers[0]["payload"] == {"request_id": request_id, "answer": "keep both"}
