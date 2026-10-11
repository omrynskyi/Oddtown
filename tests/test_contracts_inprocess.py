"""Step 2: the transport and registry contracts, with agents in one process.

Runs against local+memory and http+file. Cross-process runs, including SLIM
and the Directory, live in test_contracts_processes.py.
"""

import asyncio

import pytest

from registry.memory_registry import FileRegistry, MemoryRegistry
from transport.adapter import DeadPeerError
from transport.http_fallback import HttpTransport
from transport.local import LocalTransport
from transport.town import Town


def make_backends(kind, tmp_path):
    if kind == "local":
        return LocalTransport, lambda: MemoryRegistry()
    folder = tmp_path / "registry"
    return HttpTransport, lambda: FileRegistry(folder)


async def spawn(kind, tmp_path, agent_id, kind_of_record="character"):
    transport_cls, registry_factory = make_backends(kind, tmp_path)
    inbox: list[dict] = []

    async def handler(envelope):
        inbox.append(envelope)

    town = Town(agent_id, transport_cls(agent_id), registry_factory())
    await town.join({"kind": kind_of_record, "name": agent_id.title()}, handler)
    return town, inbox


async def wait_for(predicate, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.01)


BACKENDS = ["local", "http"]


@pytest.mark.parametrize("kind", BACKENDS)
async def test_two_agents_find_each_other_and_talk(kind, tmp_path):
    a, _ = await spawn(kind, tmp_path, "char_a")
    b, b_inbox = await spawn(kind, tmp_path, "char_b")
    try:
        peers = await a.characters()
        assert [p["id"] for p in peers] == ["char_b"]

        sent = await a.send("char_b", {"type": "idea.share", "body": {"text": "oak chest of beans"}})
        await wait_for(lambda: b_inbox)
        got = b_inbox[0]
        assert got["type"] == "idea.share"
        assert got["body"] == {"text": "oak chest of beans"}
        assert got["from"] == "char_a" and got["to"] == "char_b"
        assert got["msg_id"] == sent["msg_id"]
    finally:
        await a.leave()
        await b.leave()


@pytest.mark.parametrize("kind", BACKENDS)
async def test_broadcast_reaches_everyone_but_sender(kind, tmp_path):
    towns = [await spawn(kind, tmp_path, f"char_{i}") for i in range(4)]
    world, world_inbox = await spawn(kind, tmp_path, "world", kind_of_record="world")
    try:
        sender, sender_inbox = towns[0]
        dead = await sender.broadcast({"type": "endorse", "body": {"branch_id": "br_1"}})
        assert dead == []
        await wait_for(lambda: all(len(inbox) == 1 for _, inbox in towns[1:]) and world_inbox)
        assert sender_inbox == []
        assert all(inbox[0]["to"] is None and inbox[0]["from"] == "char_0" for _, inbox in towns[1:])
        # characters() leaves the world service out
        assert "world" not in {p["id"] for p in await sender.characters()}
    finally:
        for town, _ in towns:
            await town.leave()
        await world.leave()


@pytest.mark.parametrize("kind", BACKENDS)
async def test_send_to_departed_agent_bounces(kind, tmp_path):
    a, _ = await spawn(kind, tmp_path, "char_a")
    b, _ = await spawn(kind, tmp_path, "char_b")
    await b.leave()
    try:
        with pytest.raises(DeadPeerError):
            await a.send("char_b", {"type": "idea.share", "body": {}})
    finally:
        await a.leave()


@pytest.mark.parametrize("kind", BACKENDS)
async def test_crashed_agent_bounces_and_its_record_is_dropped(kind, tmp_path):
    a, _ = await spawn(kind, tmp_path, "char_a")
    b, _ = await spawn(kind, tmp_path, "char_b")
    # A crash: the transport dies but the stale record stays behind.
    await b.transport.close()
    assert await a.registry.get("char_b") is not None
    try:
        with pytest.raises(DeadPeerError):
            await a.send("char_b", {"type": "idea.share", "body": {}})
        assert await a.registry.get("char_b") is None
        # and a broadcast after that just skips it
        assert await a.broadcast({"type": "rest", "body": {}}) == []
    finally:
        await a.leave()


@pytest.mark.parametrize("kind", BACKENDS)
async def test_broadcast_reports_and_drops_crashed_peers(kind, tmp_path):
    a, _ = await spawn(kind, tmp_path, "char_a")
    b, b_inbox = await spawn(kind, tmp_path, "char_b")
    c, _ = await spawn(kind, tmp_path, "char_c")
    await c.transport.close()
    try:
        dead = await a.broadcast({"type": "endorse", "body": {}})
        assert dead == ["char_c"]
        await wait_for(lambda: b_inbox)
        assert await a.registry.get("char_c") is None
    finally:
        await a.leave()
        await b.leave()


async def test_registry_search_filters_and_replaces(tmp_path):
    for registry in (MemoryRegistry(), FileRegistry(tmp_path / "reg")):
        await registry.register({"id": "x", "kind": "character", "address": "a1", "traits": {"risk": 0.5}})
        await registry.register({"id": "y", "kind": "world", "address": "a2"})
        await registry.register({"id": "x", "kind": "character", "address": "a3", "traits": {"risk": 0.5}})
        assert [r["address"] for r in await registry.search(kind="character")] == ["a3"]
        assert len(await registry.search()) == 2
        assert await registry.remove("x") is True
        assert await registry.remove("x") is False  # removing twice is fine, but only the first one wins
        assert [r["id"] for r in await registry.search()] == ["y"]
        with pytest.raises(ValueError):
            await registry.register({"id": "z"})


async def test_town_that_does_not_forget_dead_peers_keeps_their_records(tmp_path):
    from transport.town import Town

    a = Town("world", LocalTransport("world"), MemoryRegistry(), forget_dead=False)
    await a.join({"kind": "world"}, lambda e: asyncio.sleep(0))
    b, _ = await spawn("local", tmp_path, "char_b")
    await b.transport.close()  # crash
    try:
        assert await a.broadcast({"type": "world.event", "body": {}}) == ["char_b"]
        with pytest.raises(DeadPeerError):
            await a.send("char_b", {"type": "x", "body": {}})
        assert await a.registry.get("char_b") is not None
    finally:
        await a.leave()


@pytest.mark.parametrize("kind", BACKENDS)
async def test_only_one_agent_gets_to_handle_a_crash(kind, tmp_path):
    """Several agents bounce off the same crashed peer at once. Exactly one gets a
    real bounce and does the pick; the rest are told it's already handled."""
    from transport.town import NOT_IN_REGISTRY

    towns = [(await spawn(kind, tmp_path, f"char_{i}"))[0] for i in range(4)]
    victim, _ = await spawn(kind, tmp_path, "char_victim")
    await victim.transport.close()  # crash, record left behind
    try:
        async def try_send(town):
            try:
                await town.send("char_victim", {"type": "idea.share", "body": {}})
            except DeadPeerError as exc:
                return exc.reason

        async def try_broadcast(town):
            return "bounced" if "char_victim" in await town.broadcast({"type": "rest", "body": {}}) else NOT_IN_REGISTRY

        reasons = await asyncio.gather(
            try_send(towns[0]), try_send(towns[1]), try_broadcast(towns[2]), try_broadcast(towns[3])
        )
        assert sum(r != NOT_IN_REGISTRY for r in reasons) == 1, reasons
        assert await towns[0].registry.get("char_victim") is None
    finally:
        for town in towns:
            await town.leave()
