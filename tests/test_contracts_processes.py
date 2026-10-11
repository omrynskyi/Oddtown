"""Steps 2 and 4: the same contract, with every agent in its own process.

http+file needs nothing running. The slim cases need docker compose up.
"""

import uuid

import pytest

from tests.procs import Peer, wait_for

BACKENDS = [
    pytest.param({"TRANSPORT": "http", "REGISTRY": "file"}, id="http+file"),
    pytest.param({"TRANSPORT": "slim", "REGISTRY": "file"}, id="slim+file", marks=pytest.mark.infra),
    pytest.param({"TRANSPORT": "slim", "REGISTRY": "agntcy"}, id="slim+agntcy", marks=pytest.mark.infra),
]


@pytest.fixture
def town_env(request, tmp_path):
    env = dict(request.param)
    env["REGISTRY_DIR"] = str(tmp_path / "registry")
    env["ODDTOWN_TOWN"] = f"test-{uuid.uuid4().hex[:8]}"
    return env


@pytest.fixture
async def peers(town_env):
    started: list[Peer] = []

    async def start(agent_id, kind="character"):
        peer = await Peer.start(agent_id, town_env, kind)
        started.append(peer)
        return peer

    yield start
    for peer in started:
        await peer.stop()


@pytest.mark.parametrize("town_env", BACKENDS, indirect=True)
async def test_two_processes_find_each_other_and_talk(peers):
    a = await peers("char_a")
    b = await peers("char_b")
    assert (await a.call("characters"))["ids"] == ["char_b"]
    result = await a.call("send", to="char_b", envelope={"type": "idea.share", "body": {"text": "oak chest"}})
    assert result["ok"]
    await wait_for(lambda: b.inbox)
    assert b.inbox[0]["body"] == {"text": "oak chest"}
    assert b.inbox[0]["from"] == "char_a"
    # and back the other way
    assert (await b.call("send", to="char_a", envelope={"type": "idea.reaction", "body": {"action": "twist"}}))["ok"]
    await wait_for(lambda: a.inbox)
    assert a.inbox[0]["type"] == "idea.reaction"


@pytest.mark.parametrize("town_env", BACKENDS, indirect=True)
async def test_broadcast_reaches_every_process(peers):
    town = [await peers(f"char_{i}") for i in range(4)]
    world = await peers("world", kind="world")
    result = await town[0].call("broadcast", envelope={"type": "endorse", "body": {"branch_id": "br_1"}})
    assert result == {**result, "ok": True, "dead": []}
    await wait_for(lambda: all(len(p.inbox) == 1 for p in town[1:]) and world.inbox)
    assert town[0].inbox == []
    assert "world" not in (await town[0].call("characters"))["ids"]


@pytest.mark.parametrize("town_env", BACKENDS, indirect=True)
async def test_killed_process_bounces_and_is_dropped(peers):
    a = await peers("char_a")
    b = await peers("char_b")
    c = await peers("char_c")
    await b.crash()  # kill -9, its record is left behind
    result = await a.call("send", to="char_b", envelope={"type": "idea.share", "body": {}})
    assert result == {**result, "ok": False, "dead_peer": "char_b"}
    assert (await a.call("characters"))["ids"] == ["char_c"]
    assert (await c.call("characters"))["ids"] == ["char_a"]


@pytest.mark.parametrize("town_env", BACKENDS, indirect=True)
async def test_broadcast_names_and_drops_killed_processes(peers):
    a = await peers("char_a")
    b = await peers("char_b")
    c = await peers("char_c")
    await c.crash()
    result = await a.call("broadcast", envelope={"type": "rest", "body": {}})
    assert result["dead"] == ["char_c"]
    await wait_for(lambda: b.inbox)
    assert (await b.call("characters"))["ids"] == ["char_a"]


@pytest.mark.parametrize("town_env", BACKENDS, indirect=True)
async def test_agent_that_leaves_is_gone(peers):
    a = await peers("char_a")
    b = await peers("char_b")
    await b.stop()
    assert (await a.call("characters"))["ids"] == []
    result = await a.call("send", to="char_b", envelope={"type": "idea.share", "body": {}})
    assert result["ok"] is False
