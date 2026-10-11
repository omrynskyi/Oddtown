"""Plays one whole run against a world service and waits for it to end.

    .venv/bin/python -m research.run "a coffee subscription" [--cast personas/x.json] [--world http://localhost:8201] [--budget 600]

Prints the run id when it's over, so `research.metrics` can score it.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("seed")
    parser.add_argument("--cast")
    parser.add_argument("--world", default="http://localhost:8201")
    parser.add_argument("--budget", type=float, default=600)
    args = parser.parse_args()

    body = {"seed": args.seed, "time_budget_s": args.budget, "ready_timeout_s": 120}
    if args.cast:
        body["cast"] = json.loads(Path(args.cast).read_text())
    with httpx.Client(base_url=args.world, timeout=60) as http:
        run_id = http.post("/runs", json=body).raise_for_status().json()["run_id"]
        print(run_id, flush=True)
        while http.get(f"/runs/{run_id}").json()["state"] == "preparing":
            time.sleep(1)
        http.post(f"/runs/{run_id}/start").raise_for_status()
        started = time.time()
        while True:
            state = http.get(f"/runs/{run_id}").json()
            if state["state"] in ("ended", "failed"):
                break
            time.sleep(5)
        print(f"{run_id} {state['state']} after {time.time() - started:.0f}s", flush=True)


if __name__ == "__main__":
    main()
