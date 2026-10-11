"""In-process transport on asyncio queues. For tests and single-process dev runs."""

from __future__ import annotations

import asyncio
import json

from transport.adapter import DeadPeerError, Envelope, Handler, Transport

# Every LocalTransport in this process shares one switchboard.
_inboxes: dict[str, asyncio.Queue] = {}


class LocalTransport(Transport):
    def __init__(self, agent_id: str):
        super().__init__(agent_id)
        self._task: asyncio.Task | None = None

    @property
    def address(self) -> str:
        return f"local://{self.agent_id}"

    async def start(self, handler: Handler) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        _inboxes[self.agent_id] = queue

        async def pump():
            while True:
                envelope = await queue.get()
                try:
                    await handler(envelope)
                except Exception:  # a bad handler must not kill the inbox
                    import logging

                    logging.getLogger(__name__).exception("handler failed")

        self._task = asyncio.create_task(pump())

    async def send(self, to: str, address: str, envelope: Envelope) -> None:
        queue = _inboxes.get(to)
        if queue is None:
            raise DeadPeerError(to, "no local inbox")
        # Round-trip through JSON so local runs catch the same serialization bugs as SLIM.
        queue.put_nowait(json.loads(json.dumps(envelope)))

    async def close(self) -> None:
        _inboxes.pop(self.agent_id, None)
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
