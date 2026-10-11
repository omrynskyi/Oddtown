"""Plain HTTP transport. The fallback when SLIM won't cooperate, and the way to
run separate agent processes with no Docker.

Each agent serves POST /inbox/{agent_id} on a free local port and answers 200
once the envelope is queued. Anything else counts as a bounce.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from transport.adapter import DeadPeerError, Envelope, Handler, Transport

log = logging.getLogger(__name__)

SEND_TIMEOUT = float(os.environ.get("HTTP_SEND_TIMEOUT", "3"))


class HttpTransport(Transport):
    def __init__(self, agent_id: str, host: str = "127.0.0.1"):
        super().__init__(agent_id)
        self.host = host
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, 0))
        self.port = self._sock.getsockname()[1]
        self._server: uvicorn.Server | None = None
        self._serve_task: asyncio.Task | None = None
        self._pump_task: asyncio.Task | None = None
        self._client = httpx.AsyncClient(timeout=SEND_TIMEOUT)

    @property
    def address(self) -> str:
        return f"http://{self.host}:{self.port}/inbox/{self.agent_id}"

    async def start(self, handler: Handler) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        app = FastAPI()

        @app.post("/inbox/{agent_id}")
        async def inbox(agent_id: str, request: Request):
            if agent_id != self.agent_id:
                return JSONResponse({"error": "wrong agent"}, status_code=404)
            queue.put_nowait(await request.json())
            return {"ok": True}

        async def pump():
            while True:
                envelope = await queue.get()
                try:
                    await handler(envelope)
                except Exception:
                    log.exception("handler failed")

        config = uvicorn.Config(app, log_level="warning", lifespan="off")
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None
        self._serve_task = asyncio.create_task(self._server.serve(sockets=[self._sock]))
        self._pump_task = asyncio.create_task(pump())
        while not self._server.started:
            if self._serve_task.done():
                self._serve_task.result()
            await asyncio.sleep(0.01)

    async def send(self, to: str, address: str, envelope: Envelope) -> None:
        if not address.startswith("http"):
            raise DeadPeerError(to, f"not an http address: {address}")
        try:
            response = await self._client.post(address, json=envelope)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            raise DeadPeerError(to, type(exc).__name__) from exc
        if response.status_code != 200:
            raise DeadPeerError(to, f"HTTP {response.status_code}")

    async def close(self) -> None:
        if self._server:
            self._server.should_exit = True
        for task in (self._serve_task, self._pump_task):
            if task is None:
                continue
            if task is self._pump_task:
                task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        await self._client.aclose()
        self._sock.close()
