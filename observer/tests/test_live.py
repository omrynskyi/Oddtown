"""Tests against a real observer process, the way agents and the UI will use it."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest
from websockets.sync.client import connect

from observer.client import emit

REPO = Path(__file__).resolve().parents[2]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def start_observer(tmp_path):
    """Start an observer on a database file. Call it again to restart on the same file."""
    db = tmp_path / "observer.db"
    procs = []

    def start():
        for p in procs:
            p.terminate()
            p.wait(5)
        port = free_port()
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "observer.app:app", "--port", str(port), "--log-level", "warning"],
            cwd=REPO,
            env={**os.environ, "OBSERVER_DB": str(db)},
        )
        procs.append(proc)
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 10
        while True:
            try:
                urllib.request.urlopen(f"{url}/runs", timeout=0.5)
                return url
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError("observer did not start")
                time.sleep(0.1)

    yield start
    for p in procs:
        p.terminate()
        p.wait(5)


def test_seq_survives_a_restart(start_observer):
    url = start_observer()
    assert emit("run_1", "run.started", url=url)
    assert emit("run_1", "run.started", url=url)
    url = start_observer()
    assert emit("run_1", "run.started", url=url)
    events = json.load(urllib.request.urlopen(f"{url}/runs/run_1/events"))
    assert [e["seq"] for e in events] == [1, 2, 3]


def test_joining_mid_run_gets_every_event_once_in_order(start_observer):
    url = start_observer()
    total = 300
    halfway = threading.Event()

    def agent():
        for i in range(total):
            assert emit("race", "endorse", {"branch_id": f"br_{i}"}, agent_id="char_1", url=url)
            if i == total // 3:
                halfway.set()

    producer = threading.Thread(target=agent)
    producer.start()
    assert halfway.wait(10)

    seqs = []
    with connect(url.replace("http", "ws") + "/ws/race") as ws:
        while len(seqs) < total:
            seqs.append(json.loads(ws.recv(timeout=10))["seq"])
        producer.join()
        with pytest.raises(TimeoutError):
            ws.recv(timeout=0.5)

    assert seqs == list(range(1, total + 1))


def test_emit_from_another_process_reaches_the_ui(start_observer):
    url = start_observer()
    with connect(url.replace("http", "ws") + "/ws/proc") as ws:
        code = (
            "import sys; from observer.client import emit; "
            f"sys.exit(0 if emit('proc', 'world.event', {{'kind': 'seed', 'text': 'a coffee subscription'}}, url='{url}') else 1)"
        )
        assert subprocess.run([sys.executable, "-c", code], cwd=REPO).returncode == 0
        event = json.loads(ws.recv(timeout=5))
    assert event["type"] == "world.event" and event["seq"] == 1
    assert event["payload"] == {"kind": "seed", "text": "a coffee subscription"}
