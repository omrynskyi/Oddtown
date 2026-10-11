"""Registry contract. Agents only ever call register, search and remove.

A record is a plain dict. Required keys: `id`, `kind` ("character" or "world")
and `address` (from the agent's transport). Characters also carry `name`,
`traits` (six floats in [-1, 1]), `lineage` and the full `persona`.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

Record = dict

REQUIRED_KEYS = ("id", "kind", "address")


def check_record(record: Record) -> None:
    missing = [key for key in REQUIRED_KEYS if not record.get(key)]
    if missing:
        raise ValueError(f"registry record is missing {', '.join(missing)}")


def matches(record: Record, match: dict) -> bool:
    return all(record.get(key) == value for key, value in match.items())


class Registry(ABC):
    @abstractmethod
    async def register(self, record: Record) -> None:
        """Publish a record. Registering the same id again replaces it."""

    @abstractmethod
    async def search(self, **match) -> list[Record]:
        """Living records whose top-level fields equal every `match` value.

        No arguments returns everything. The Directory can't filter numeric
        ranges, so rank by trait locally on what this returns.
        """

    @abstractmethod
    async def remove(self, agent_id: str) -> None:
        """Delete a record. Removing an unknown id is not an error."""

    async def get(self, agent_id: str) -> Record | None:
        found = await self.search(id=agent_id)
        return found[0] if found else None

    async def close(self) -> None:
        pass


def create_registry(kind: str | None = None) -> Registry:
    """Pick the backend from REGISTRY (memory, file or agntcy). Agent code never changes."""
    kind = (kind or os.environ.get("REGISTRY", "memory")).lower()
    if kind == "memory":
        from registry.memory_registry import MemoryRegistry

        return MemoryRegistry()
    if kind == "file":
        from registry.memory_registry import FileRegistry

        return FileRegistry(os.environ.get("REGISTRY_DIR", ".oddtown/registry"))
    if kind == "agntcy":
        from registry.agntcy_dir import AgntcyDirectory

        return AgntcyDirectory(os.environ.get("DIRECTORY_ADDRESS", "localhost:8888"))
    raise ValueError(f"unknown REGISTRY {kind!r}, expected memory, file or agntcy")
