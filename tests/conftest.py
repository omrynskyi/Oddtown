import os
import socket
from pathlib import Path

import pytest

from registry.memory_registry import MemoryRegistry

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv():
    """Read .env without overriding anything already set in the shell."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


def _port_open(hostport: str) -> bool:
    host, port = hostport.replace("http://", "").rsplit(":", 1)
    try:
        with socket.create_connection((host, int(port)), timeout=1):
            return True
    except OSError:
        return False


def infra_up() -> bool:
    return _port_open(os.environ.get("SLIM_ENDPOINT", "http://localhost:46357")) and _port_open(
        os.environ.get("DIRECTORY_ADDRESS", "localhost:8888")
    )


def pytest_collection_modifyitems(config, items):
    if infra_up():
        return
    skip = pytest.mark.skip(reason="SLIM or the Directory is down, run: docker compose up -d --wait")
    for item in items:
        if "infra" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def clean_memory_registry():
    MemoryRegistry.reset()
    yield
    MemoryRegistry.reset()
