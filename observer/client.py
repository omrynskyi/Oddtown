"""The one function agents and the world service call to report what happened.

    from observer.client import emit, aemit

    emit("run_1", "agent.decision", {"action": "twist", "say": "Make it last fifty years"},
         agent_id="char_7f3a")
    await aemit("run_1", "agent.decision", {...}, agent_id="char_7f3a")

It never raises. If the event breaks the contract, or the observer is down or
slow, it logs a warning and returns False, so the observer can never crash or
stall an agent. Set OBSERVER_URL to point somewhere other than localhost:8100.
"""

import asyncio
import json
import logging
import os
import time
import urllib.request
from typing import Any

from pydantic import ValidationError

from observer.events import parse_event

log = logging.getLogger(__name__)

DEFAULT_URL = "http://localhost:8100"


def emit(
    run_id: str,
    type: str,
    payload: dict[str, Any] | None = None,
    agent_id: str | None = None,
    *,
    url: str | None = None,
    timeout: float = 1.0,
) -> bool:
    event = {"run_id": run_id, "ts": time.time(), "type": type, "agent_id": agent_id, "payload": payload or {}}
    try:
        parse_event(event)
    except ValidationError as e:
        log.warning("not emitting invalid %s event: %s", type, e)
        return False

    base = url or os.environ.get("OBSERVER_URL", DEFAULT_URL)
    request = urllib.request.Request(
        f"{base}/events",
        data=json.dumps(event).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except Exception as e:
        log.warning("could not emit %s event to %s: %s", type, base, e)
        return False


async def aemit(
    run_id: str,
    type: str,
    payload: dict[str, Any] | None = None,
    agent_id: str | None = None,
    *,
    url: str | None = None,
    timeout: float = 1.0,
) -> bool:
    return await asyncio.to_thread(emit, run_id, type, payload, agent_id, url=url, timeout=timeout)
