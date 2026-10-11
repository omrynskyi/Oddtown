"""Tests against a real observer process, the way agents and the UI will use it."""

import json
import subprocess
import sys
import threading
import urllib.request

import pytest
from websockets.sync.client import connect

from observer.client import emit
from observer.tests.conftest import REPO


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
