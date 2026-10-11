import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

# Importing observer.app builds the module-level app. Keep it off the real observer.db.
os.environ["OBSERVER_DB"] = ":memory:"

REPO = Path(__file__).resolve().parents[2]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def start_server():
    """Start a uvicorn app in its own process. Starting the same name again restarts it."""
    procs: dict[str, subprocess.Popen] = {}

    def stop(name):
        proc = procs.pop(name, None)
        if proc:
            proc.terminate()
            proc.wait(5)

    def start(name, app, env=None):
        stop(name)
        port = free_port()
        procs[name] = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", app, "--port", str(port), "--log-level", "warning"],
            cwd=REPO,
            env={**os.environ, **(env or {})},
        )
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 10
        while True:
            try:
                urllib.request.urlopen(f"{url}/openapi.json", timeout=0.5)
                return url
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError(f"{name} did not start")
                time.sleep(0.1)

    yield start
    for name in list(procs):
        stop(name)


@pytest.fixture
def start_observer(start_server, tmp_path):
    """Start the observer on a test database. Call it again to restart on the same file."""
    db = tmp_path / "observer.db"
    return lambda: start_server("observer", "observer.app:app", {"OBSERVER_DB": str(db)})
