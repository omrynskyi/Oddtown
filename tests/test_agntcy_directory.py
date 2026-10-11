"""Step 4: the registry contract against the real AGNTCY Directory."""

import os
import uuid

import pytest

from registry.agntcy_dir import AgntcyDirectory

pytestmark = pytest.mark.infra


@pytest.fixture
async def directory():
    d = AgntcyDirectory(os.environ.get("DIRECTORY_ADDRESS", "localhost:8888"), town=f"test-{uuid.uuid4().hex[:8]}")
    yield d
    for record in await d.search():
        await d.remove(record["id"])


async def test_register_search_replace_remove(directory):
    persona = {"one_liner": "Retired lighthouse keeper", "traits": {"risk": 0.2, "scale": -0.6}}
    await directory.register({"id": "char_a", "kind": "character", "address": "slim://x/a", "persona": persona})
    await directory.register({"id": "world", "kind": "world", "address": "slim://x/w"})
    await directory.register({"id": "char_a", "kind": "character", "address": "slim://x/a2", "persona": persona})

    characters = await directory.search(kind="character")
    assert [r["address"] for r in characters] == ["slim://x/a2"]
    assert characters[0]["persona"]["traits"] == {"risk": 0.2, "scale": -0.6}
    assert {r["id"] for r in await directory.search()} == {"char_a", "world"}
    assert len(await directory._cids_for("char_a")) == 1  # the replaced record was deleted

    await directory.remove("char_a")
    await directory.remove("char_a")
    assert [r["id"] for r in await directory.search()] == ["world"]
    assert await directory.get("char_a") is None


async def test_towns_do_not_see_each_other(directory):
    other = AgntcyDirectory(directory.address, town=directory.town + "-other")
    await directory.register({"id": "char_a", "kind": "character", "address": "a"})
    await other.register({"id": "char_b", "kind": "character", "address": "b"})
    try:
        assert [r["id"] for r in await directory.search()] == ["char_a"]
        assert [r["id"] for r in await other.search()] == ["char_b"]
    finally:
        await other.remove("char_b")


async def test_concurrent_removes_of_the_same_record(directory):
    import asyncio

    await directory.register({"id": "char_a", "kind": "character", "address": "a"})
    others = [AgntcyDirectory(directory.address, town=directory.town) for _ in range(4)]
    await asyncio.gather(*(d.remove("char_a") for d in [directory, *others]))
    assert await directory.search() == []
