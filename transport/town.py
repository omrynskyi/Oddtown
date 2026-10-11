"""What a character process holds: its transport plus the registry.

Town is the only thing agent code needs from the platform. It fills in the
envelope header, finds peers by id, and when a send bounces it deletes the
dead peer's record so nobody routes to it again.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from registry.adapter import Record, Registry
from transport.adapter import DeadPeerError, Envelope, Handler, Transport

log = logging.getLogger(__name__)

# The reason a send gives when the peer's record is already gone: it left
# normally, or another agent noticed its crash first and is handling it.
NOT_IN_REGISTRY = "not in the registry"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Town:
    def __init__(self, agent_id: str, transport: Transport, registry: Registry, forget_dead: bool = True):
        self.agent_id = agent_id
        self.transport = transport
        self.registry = registry
        # Characters delete a dead peer's record when a send bounces, and the one
        # that does it runs the replacement pick. The world service turns this off,
        # so it never beats a character to a crash and leaves the town a member short.
        self.forget_dead = forget_dead

    @classmethod
    def from_env(cls, agent_id: str, forget_dead: bool = True) -> "Town":
        from registry.adapter import create_registry
        from transport.adapter import create_transport

        return cls(agent_id, create_transport(agent_id), create_registry(), forget_dead=forget_dead)

    async def join(self, record: Record, handler: Handler) -> None:
        """Start receiving, then publish this agent's record with its address."""
        await self.transport.start(handler)
        await self.registry.register({**record, "id": self.agent_id, "address": self.transport.address})

    async def leave(self) -> None:
        """Delete this agent's record and close its transport. Call it on death."""
        await self.registry.remove(self.agent_id)
        await self.transport.close()
        await self.registry.close()

    def _stamp(self, envelope: Envelope, to: str | None) -> Envelope:
        return {
            "msg_id": str(uuid.uuid4()),
            "sent_at": now_iso(),
            **envelope,
            "from": self.agent_id,
            "to": to,
        }

    async def send(self, to: str, envelope: Envelope) -> Envelope:
        """Point to point. Raises DeadPeerError if the peer is gone."""
        record = await self.registry.get(to)
        if record is None:
            raise DeadPeerError(to, NOT_IN_REGISTRY)
        stamped = self._stamp(envelope, to)
        try:
            await self.transport.send(to, record["address"], stamped)
        except DeadPeerError as exc:
            if self.forget_dead and not await self._forget(to):
                # Another agent removed the record first, so the crash is theirs to handle.
                raise DeadPeerError(to, NOT_IN_REGISTRY) from exc
            raise
        return stamped

    async def _forget(self, peer: str) -> bool:
        """Delete a dead peer's record. True if this agent got there first."""
        if not self.forget_dead:
            return False
        try:
            return await self.registry.remove(peer)
        except Exception:
            log.exception("could not remove %s from the registry", peer)
            return False

    async def broadcast(self, envelope: Envelope) -> list[str]:
        """To every living record, the world service included. Returns who bounced.

        With forget_dead on, that's only the peers whose record this agent
        removed first, the crashes that are this agent's to handle.
        """
        records = await self.registry.search()
        recipients = {r["id"]: r["address"] for r in records if r["id"] != self.agent_id}
        dead = await self.transport.broadcast(recipients, self._stamp(envelope, None))
        if not self.forget_dead:
            return dead
        return [peer for peer in dead if await self._forget(peer)]

    async def characters(self) -> list[Record]:
        """Every living character except this one."""
        return [r for r in await self.registry.search(kind="character") if r["id"] != self.agent_id]
