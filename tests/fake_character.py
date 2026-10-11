"""A scripted stand-in for agents.character, used to test the platform.

Run by the launcher as `python -m tests.fake_character`. It behaves like a
character from the platform's point of view: it joins, says hello, endorses
when the seed arrives, and leaves on a stop event. The persona's "script"
field says what else it does:

    endorse       branch ids to back after the seed (default: its own branch)
    reason        reason sent with each endorsement
    object_to     [branch_id, reason] broadcast as an objection
    rest_after    seconds after the seed to broadcast rest
    tokens        tokens to report to the world after the seed
    die_after     seconds after the seed to post last words and leave
    spawn         a persona to ask the world to start, sent just before dying
    send_to       [peer_id, seconds] send an idea.share after that delay
"""

import asyncio
import json
import os
import sys

from observer.events import emit
from transport.adapter import DeadPeerError
from transport.town import Town


async def main() -> None:
    agent_id = os.environ["AGENT_ID"]
    persona = json.loads(open(os.environ["PERSONA_FILE"]).read())
    script = persona.get("script", {})
    town = Town.from_env(agent_id)
    done = asyncio.Event()
    tasks: set[asyncio.Task] = set()

    def later(coro):
        task = asyncio.create_task(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        task.add_done_callback(lambda t: t.cancelled() or not t.exception() or emit(
            "test.error", agent_id, {"error": repr(t.exception())}))

    async def world_id() -> str:
        worlds = await town.registry.search(kind="world")
        return worlds[0]["id"]

    async def die():
        await town.broadcast({"type": "agent.last_words", "body": {"text": f"{persona['name']} signs off"}})
        if script.get("spawn"):
            await town.send(await world_id(), {"type": "spawn.request", "body": {"persona": script["spawn"]}})
        await town.leave()
        done.set()

    async def after_seed(seed: str):
        for branch_id in script.get("endorse", [f"br_{agent_id}"]):
            branch = {"branch_id": branch_id, "text": f"{branch_id} on {seed}", "author": agent_id}
            await town.broadcast({"type": "endorse", "body": {"branch": branch, "reason": script.get("reason", "")}})
        if script.get("object_to"):
            branch_id, reason = script["object_to"]
            await town.broadcast({"type": "objection", "body": {"branch_id": branch_id, "reason": reason}})
        if script.get("tokens"):
            await town.send(await world_id(), {"type": "usage.report", "body": {"tokens": script["tokens"]}})
        if script.get("send_to"):
            peer, delay = script["send_to"]
            await asyncio.sleep(delay)
            try:
                await town.send(peer, {"type": "idea.share", "body": {"text": "hello?"}})
                emit("test.sent", agent_id, {"to": peer, "ok": True})
            except DeadPeerError:
                emit("test.sent", agent_id, {"to": peer, "ok": False})
        if "rest_after" in script:
            await asyncio.sleep(script["rest_after"])
            await town.broadcast({"type": "rest", "body": {}})
        if "die_after" in script:
            await asyncio.sleep(script["die_after"])
            await die()

    async def handler(envelope: dict):
        emit("test.received", agent_id, envelope)
        body = envelope.get("body") or {}
        if envelope.get("type") == "world.event" and body.get("kind") == "seed":
            later(after_seed(body["text"]))
        elif envelope.get("type") == "world.event" and body.get("kind") == "stop":
            await town.leave()
            done.set()

    await town.join({"kind": "character", "name": persona["name"], "persona": persona}, handler)
    await town.broadcast({"type": "agent.hello", "body": {"name": persona["name"]}})
    await done.wait()
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
