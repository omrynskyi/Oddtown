"""Drive tests/peer_agent.py processes from async tests."""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_cids = itertools.count(1)


class Peer:
    def __init__(self, agent_id: str, proc: asyncio.subprocess.Process):
        self.agent_id = agent_id
        self.proc = proc
        self.inbox: list[dict] = []
        self.address: str | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._ready = asyncio.get_running_loop().create_future()
        self._reader = asyncio.create_task(self._read())

    @classmethod
    async def start(cls, agent_id: str, env: dict, kind: str = "character", timeout: float = 20) -> "Peer":
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(ROOT / "tests" / "peer_agent.py"), agent_id, kind,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, **env}, cwd=ROOT,
        )
        peer = cls(agent_id, proc)
        await asyncio.wait_for(asyncio.shield(peer._ready), timeout)
        return peer

    async def _read(self):
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                break
            if not line.startswith(b"@@"):
                continue  # library logging that went to stdout
            msg = json.loads(line[2:])
            if msg.get("event") == "ready":
                self.address = msg["address"]
                self._ready.set_result(True)
            elif msg.get("event") == "received":
                self.inbox.append(msg["envelope"])
            elif msg.get("cid") in self._pending:
                self._pending.pop(msg["cid"]).set_result(msg)
        if not self._ready.done():
            self._ready.set_exception(RuntimeError(f"{self.agent_id} exited before it was ready"))

    async def call(self, op: str, timeout: float = 20, **kwargs) -> dict:
        cid = next(_cids)
        future = asyncio.get_running_loop().create_future()
        self._pending[cid] = future
        self.proc.stdin.write((json.dumps({"op": op, "cid": cid, **kwargs}) + "\n").encode())
        await self.proc.stdin.drain()
        return await asyncio.wait_for(future, timeout)

    async def crash(self):
        self.proc.kill()
        await self.proc.wait()

    async def stop(self):
        if self.proc.returncode is None:
            try:
                await self.call("leave", timeout=10)
            except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), 10)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        self._reader.cancel()


async def wait_for(predicate, timeout=10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.05)
