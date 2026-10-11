"""Transport contract. Agents only ever call these methods.

Every backend (local, http, slim) delivers JSON-serializable envelopes and
acknowledges each delivery, so a send that is not acknowledged means the peer
is gone. That is how a crashed agent gets noticed without a central monitor.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Awaitable, Callable

Envelope = dict
Handler = Callable[[Envelope], Awaitable[None]]


class DeadPeerError(Exception):
    """A send to this peer bounced. Treat the peer as dead."""

    def __init__(self, peer_id: str, reason: str = ""):
        super().__init__(f"peer {peer_id} is unreachable{': ' + reason if reason else ''}")
        self.peer_id = peer_id
        self.reason = reason


class Transport(ABC):
    """One transport per agent process, bound to that agent's id."""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id

    @property
    @abstractmethod
    def address(self) -> str:
        """Where peers reach this agent. Goes into the agent's registry record."""

    @abstractmethod
    async def start(self, handler: Handler) -> None:
        """Start receiving. `handler` runs once per incoming envelope, after it is acked."""

    @abstractmethod
    async def send(self, to: str, address: str, envelope: Envelope) -> None:
        """Deliver one envelope to one peer. Raises DeadPeerError if it bounces."""

    async def broadcast(self, recipients: dict[str, str], envelope: Envelope) -> list[str]:
        """Deliver to every {agent_id: address}. Returns the ids that bounced."""
        import asyncio

        targets = [(peer, addr) for peer, addr in recipients.items() if peer != self.agent_id]
        results = await asyncio.gather(
            *(self.send(peer, addr, envelope) for peer, addr in targets),
            return_exceptions=True,
        )
        dead = []
        for (peer, _), result in zip(targets, results):
            if isinstance(result, DeadPeerError):
                dead.append(peer)
            elif isinstance(result, BaseException):
                raise result
        return dead

    @abstractmethod
    async def close(self) -> None:
        """Stop receiving and release the connection."""


def create_transport(agent_id: str, kind: str | None = None) -> Transport:
    """Pick the backend from TRANSPORT (local, http or slim). Agent code never changes."""
    kind = (kind or os.environ.get("TRANSPORT", "local")).lower()
    if kind == "local":
        from transport.local import LocalTransport

        return LocalTransport(agent_id)
    if kind == "http":
        from transport.http_fallback import HttpTransport

        return HttpTransport(agent_id)
    if kind == "slim":
        from transport.slim_a2a import SlimTransport

        return SlimTransport(agent_id)
    raise ValueError(f"unknown TRANSPORT {kind!r}, expected local, http or slim")
