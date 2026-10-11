"""STUB owned by Oleg (observer). Replace with the real event schema and client.

Events use the shape {run_id, seq, ts, type, agent_id, payload}. This stub
appends them to the JSONL file in OBSERVER_LOG, or drops them if it is unset.
"""

from __future__ import annotations

import itertools
import json
import os
from datetime import datetime, timezone

_seq = itertools.count()


def emit(type: str, agent_id: str | None, payload: dict, run_id: str | None = None) -> dict:
    event = {
        "run_id": run_id or os.environ.get("RUN_ID", "dev"),
        "seq": next(_seq),
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": type,
        "agent_id": agent_id,
        "payload": payload,
    }
    path = os.environ.get("OBSERVER_LOG")
    if path:
        with open(path, "a") as f:
            f.write(json.dumps(event) + "\n")
    return event
