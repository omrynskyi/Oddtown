"""Transport over SLIM through the AGNTCY app-sdk.

Each agent gets a routable SLIM name, oddtown/<town>/<agent_id>, so two runs
can share one SLIM node. A send is a
point-to-point SLIM request that the receiver acks as soon as the envelope is
queued, so no ack means the peer is gone. Broadcasts fan out as one request per
living recipient, because SLIM group sessions need a moderator and the town
has no owner.

Sends open their own point-to-point sessions instead of going through the
SDK's request(). The SDK opens every session under one lock, so a single dead
peer stalls every other send in a broadcast until its timeout runs out.

The app-sdk keeps one SLIM connection per process, so it's one agent per
process here, which is how the launcher runs them anyway.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os

from agntcy_app_sdk.semantic.message import Message
from agntcy_app_sdk.transport.slim.transport import SLIMTransport as SdkSlimTransport
from slim_bindings import SessionConfig, SessionType

from transport.adapter import DeadPeerError, Envelope, Handler, Transport

log = logging.getLogger(__name__)

ORG = "oddtown"
ENVELOPE_TYPE = "oddtown.envelope"
ACK_TYPE = "oddtown.ack"


def slim_endpoint() -> str:
    return os.environ.get("SLIM_ENDPOINT", "http://localhost:46357")


def send_timeout() -> float:
    return float(os.environ.get("SLIM_SEND_TIMEOUT", "3"))


class SlimTransport(Transport):
    def __init__(self, agent_id: str):
        super().__init__(agent_id)
        town = os.environ.get("ODDTOWN_TOWN", "oddtown")
        self._routable = f"{ORG}/{town}/{agent_id}"
        self._sdk = SdkSlimTransport(
            routable_name=self._routable,
            endpoint=slim_endpoint(),
            shared_secret_identity=os.environ.get(
                "SLIM_SHARED_SECRET", "oddtown-local-dev-shared-secret-change-me"
            ),
            message_timeout=datetime.timedelta(seconds=10),
        )
        self._handler: Handler | None = None
        self._tasks: set[asyncio.Task] = set()

    @property
    def address(self) -> str:
        return f"slim://{self._routable}"

    async def _connect(self) -> None:
        await self._sdk.setup()

    async def start(self, handler: Handler) -> None:
        self._handler = handler
        await self._connect()
        self._sdk.set_callback(self._on_message)

    async def _on_message(self, message: Message, publish_fn=None) -> Message | None:
        if message.type != ENVELOPE_TYPE:
            return None
        try:
            envelope = json.loads(message.payload)
        except (TypeError, ValueError):
            log.warning("dropping a message that is not JSON")
            return Message(type=ACK_TYPE, payload=b'{"ok": false}')
        # Ack first, handle after, so a slow handler never looks like a dead agent.
        task = asyncio.create_task(self._run_handler(envelope))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return Message(type=ACK_TYPE, payload=b'{"ok": true}')

    async def _run_handler(self, envelope: Envelope) -> None:
        try:
            await self._handler(envelope)
        except Exception:
            log.exception("handler failed")

    async def send(self, to: str, address: str, envelope: Envelope) -> None:
        if not address.startswith("slim://"):
            raise DeadPeerError(to, f"not a slim address: {address}")
        await self._connect()
        message = Message(type=ENVELOPE_TYPE, payload=json.dumps(envelope).encode())
        message.headers["x-respond-to-source"] = "true"
        # Not asyncio.wait_for: it waits for the cancelled call to wind down, and a
        # SLIM call stuck on a dead peer doesn't, so the send would hang.
        task = asyncio.create_task(self._request(address.removeprefix("slim://"), message))
        done, _ = await asyncio.wait({task}, timeout=send_timeout())
        if not done:
            task.cancel()
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            raise DeadPeerError(to, "no ack")
        try:
            reply = task.result()
        except Exception as exc:
            raise DeadPeerError(to, str(exc)) from exc
        if reply.type != ACK_TYPE:
            raise DeadPeerError(to, f"unexpected reply {reply.type}")

    async def _request(self, routable: str, message: Message) -> Message:
        app = self._sdk._slim_app
        remote = self._sdk.build_name(routable)
        await app.set_route_async(remote, self._sdk._slim_connection_id)
        interval = datetime.timedelta(seconds=max(send_timeout() / 3, 0.2))
        ctx = await app.create_session_async(
            SessionConfig(
                session_type=SessionType.POINT_TO_POINT,
                max_retries=2,
                interval=interval,
                enable_mls=True,
                metadata={},
            ),
            remote,
        )
        session = ctx.session
        try:
            await ctx.completion.wait_async()
            await session.publish_async(message.serialize(), None, None)
            raw = await session.get_message_async(timeout=datetime.timedelta(seconds=send_timeout()))
            return Message.deserialize(raw.payload)
        finally:
            # Closing can block on a dead peer, so do it in the background.
            task = asyncio.create_task(self._close_session(session))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _close_session(self, session) -> None:
        try:
            handle = await self._sdk._slim_app.delete_session_async(session)
            await asyncio.wait_for(handle.wait_async(), 10)
        except Exception:
            pass

    async def close(self) -> None:
        self._sdk._shutdown_event.set()
        if self._sdk._listener_task:
            self._sdk._listener_task.cancel()
        for task in list(self._tasks):
            task.cancel()
        await self._sdk.close()
