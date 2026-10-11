"""Append-only event log in SQLite. The observer is the only writer."""

import json
import sqlite3
import threading
from typing import Any

from observer.events import Event


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS events (
                 run_id TEXT NOT NULL,
                 seq INTEGER NOT NULL,
                 ts REAL NOT NULL,
                 type TEXT NOT NULL,
                 agent_id TEXT,
                 payload TEXT NOT NULL,
                 PRIMARY KEY (run_id, seq))"""
        )
        self.db.commit()
        self.lock = threading.Lock()

    def append(self, event: Event) -> dict[str, Any]:
        """Store an event with the next seq for its run, and return it as sent to the UI."""
        with self.lock:
            (last,) = self.db.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM events WHERE run_id = ?", (event.run_id,)
            ).fetchone()
            stored = {**event.model_dump(), "seq": last + 1}
            self.db.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                (stored["run_id"], stored["seq"], stored["ts"], stored["type"], stored["agent_id"], json.dumps(stored["payload"])),
            )
            self.db.commit()
        return stored

    def events(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT seq, ts, type, agent_id, payload FROM events WHERE run_id = ? AND seq > ? ORDER BY seq",
                (run_id, after),
            ).fetchall()
        return [
            {"run_id": run_id, "seq": seq, "ts": ts, "type": type, "agent_id": agent_id, "payload": json.loads(payload)}
            for seq, ts, type, agent_id, payload in rows
        ]

    def runs(self) -> list[dict[str, Any]]:
        """Every run, newest first, with its seed so a recorded run is easy to find."""
        with self.lock:
            rows = self.db.execute(
                """SELECT run_id, COUNT(*), MIN(ts), MAX(ts),
                          MAX(CASE WHEN type = 'run.created' THEN json_extract(payload, '$.seed') END)
                   FROM events GROUP BY run_id ORDER BY MAX(ts) DESC"""
            ).fetchall()
        return [
            {"run_id": run_id, "events": count, "first_ts": first, "last_ts": last, "seed": seed}
            for run_id, count, first, last, seed in rows
        ]
