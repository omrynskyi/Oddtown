import asyncio
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from observer.client import aemit, emit


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_emit_posts_the_event():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.path, json.loads(body)))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"

    ok = emit("run_1", "endorse", {"branch_id": "br_1"}, agent_id="char_1", url=url)
    server.server_close()

    assert ok is True
    path, body = received[0]
    assert path == "/events"
    assert body["run_id"] == "run_1" and body["type"] == "endorse" and body["agent_id"] == "char_1"
    assert body["payload"] == {"branch_id": "br_1"}
    assert "seq" not in body


def test_emit_to_closed_port_returns_false_fast():
    start = time.monotonic()
    ok = emit("run_1", "run.started", url=f"http://127.0.0.1:{free_port()}")
    assert ok is False
    assert time.monotonic() - start < 1.5


def test_emit_to_hung_observer_times_out_in_about_a_second():
    # Accepts the connection but never answers.
    with socket.socket() as hung:
        hung.bind(("127.0.0.1", 0))
        hung.listen()
        start = time.monotonic()
        ok = emit("run_1", "run.started", url=f"http://127.0.0.1:{hung.getsockname()[1]}")
        elapsed = time.monotonic() - start
    assert ok is False
    assert 0.8 < elapsed < 2.0


def test_emit_refuses_an_invalid_event_without_raising():
    assert emit("run_1", "agent.decision", {"action": "dance", "say": "hi"}, url="http://127.0.0.1:1") is False


def test_aemit_does_not_block_the_event_loop():
    async def main():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.05)
                ticks += 1

        task = asyncio.create_task(ticker())
        ok = await aemit("run_1", "run.started", url=f"http://127.0.0.1:{free_port()}")
        await asyncio.sleep(0.2)
        task.cancel()
        return ok, ticks

    ok, ticks = asyncio.run(main())
    assert ok is False
    assert ticks >= 3
