"""Registries with no Directory behind them.

MemoryRegistry lives in one process, for tests and single-process dev runs.
FileRegistry keeps one JSON file per agent in a shared folder, so separate
agent processes on one machine can find each other with no Docker.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from registry.adapter import Record, Registry, check_record, matches

# Every MemoryRegistry in this process shares one table, like a real Directory would.
_records: dict[str, Record] = {}


class MemoryRegistry(Registry):
    async def register(self, record: Record) -> None:
        check_record(record)
        _records[record["id"]] = json.loads(json.dumps(record))

    async def search(self, **match) -> list[Record]:
        return [json.loads(json.dumps(r)) for r in _records.values() if matches(r, match)]

    async def remove(self, agent_id: str) -> None:
        _records.pop(agent_id, None)

    @staticmethod
    def reset() -> None:
        _records.clear()


class FileRegistry(Registry):
    def __init__(self, folder: str | os.PathLike):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def _path(self, agent_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in agent_id)
        return self.folder / f"{safe}.json"

    async def register(self, record: Record) -> None:
        check_record(record)
        # Write then rename, so a reader never sees half a file.
        fd, tmp = tempfile.mkstemp(dir=self.folder, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(record, f)
        os.replace(tmp, self._path(record["id"]))

    async def search(self, **match) -> list[Record]:
        found = []
        for path in sorted(self.folder.glob("*.json")):
            try:
                record = json.loads(path.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                continue  # removed or replaced while we were reading
            if matches(record, match):
                found.append(record)
        return found

    async def remove(self, agent_id: str) -> None:
        self._path(agent_id).unlink(missing_ok=True)
