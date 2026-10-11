"""A stand-in agent process for cross-process tests.

It joins the town with whatever TRANSPORT and REGISTRY the environment says,
then takes one JSON command per stdin line and answers on stdout. Received
envelopes are printed as they arrive.
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from transport.adapter import DeadPeerError  # noqa: E402
from transport.town import Town  # noqa: E402


def out(obj):
    sys.stdout.write("@@" + json.dumps(obj) + "\n")
    sys.stdout.flush()


async def main(agent_id: str, kind: str):
    town = Town.from_env(agent_id)

    async def handler(envelope):
        out({"event": "received", "envelope": envelope})

    await town.join({"kind": kind, "name": agent_id}, handler)
    out({"event": "ready", "address": town.transport.address})

    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        cmd = json.loads(line)
        op, cid = cmd["op"], cmd.get("cid")
        try:
            if op == "send":
                sent = await town.send(cmd["to"], cmd["envelope"])
                out({"cid": cid, "ok": True, "sent": sent})
            elif op == "broadcast":
                dead = await town.broadcast(cmd["envelope"])
                out({"cid": cid, "ok": True, "dead": dead})
            elif op == "characters":
                out({"cid": cid, "ok": True, "ids": [r["id"] for r in await town.characters()]})
            elif op == "leave":
                await town.leave()
                out({"cid": cid, "ok": True})
                break
            elif op == "crash":
                os._exit(1)
        except DeadPeerError as exc:
            out({"cid": cid, "ok": False, "dead_peer": exc.peer_id})
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "character"))
