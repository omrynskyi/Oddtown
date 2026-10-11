"""Registry on the AGNTCY Directory.

Every agent is one OASF record named oddtown/<town>/<agent_id>. The town, id,
kind, address and the full record ride along as annotations, so search is one
annotation query for the town and the rest is filtered locally (the Directory
can't filter numeric ranges anyway). Birth pushes and publishes the record,
death unpublishes and deletes it.

The Directory SDK is synchronous gRPC, so every call runs in a thread.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from agntcy.dir_sdk.client.client import Client
from agntcy.dir_sdk.client.config import Config
from agntcy.dir_sdk.models import core_v1, routing_v1, search_v1
from google.protobuf.json_format import MessageToDict, ParseDict

from registry.adapter import Record, Registry, check_record, matches

log = logging.getLogger(__name__)

QUERY = search_v1.RecordQueryType
OASF_SCHEMA_VERSION = "1.0.0"
SEARCH_LIMIT = 500
RETRY_DELAYS = (0.2, 0.5, 1.0, 2.0)


def _transient(exc: Exception) -> bool:
    text = str(exc)
    return "UNAVAILABLE" in text or "DEADLINE_EXCEEDED" in text or "Connection reset" in text


class AgntcyDirectory(Registry):
    def __init__(self, address: str = "localhost:8888", town: str | None = None):
        self.address = address
        # Separate towns (runs, tests) share one Directory without seeing each other.
        self.town = town or os.environ.get("ODDTOWN_TOWN", "oddtown")
        self._client: Client | None = None
        self._lock = asyncio.Lock()

    async def _call(self, fn, *args):
        # The Directory drops connections now and then when many agents hit it at
        # once (gRPC UNAVAILABLE). Those are safe to retry with a fresh client.
        for attempt, delay in enumerate(RETRY_DELAYS):
            if self._client is None:
                self._client = await asyncio.to_thread(Client, Config(server_address=self.address))
            try:
                return await asyncio.to_thread(fn, *args)
            except Exception as exc:
                if not _transient(exc) or attempt == len(RETRY_DELAYS) - 1:
                    raise
                log.debug("directory call failed, retrying: %s", exc)
                self._client = None
                await asyncio.sleep(delay)

    def _name(self, agent_id: str) -> str:
        return f"oddtown/{self.town}/{agent_id}"

    def _to_oasf(self, record: Record) -> dict:
        persona = record.get("persona") or {}
        return {
            "name": self._name(record["id"]),
            "schema_version": OASF_SCHEMA_VERSION,
            "version": "1.0.0",
            "description": persona.get("one_liner") or record.get("name") or record["id"],
            "authors": ["oddtown"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "skills": [{"id": 101}],
            "domains": [],
            "modules": [],
            "annotations": {
                "oddtown.town": self.town,
                "oddtown.id": record["id"],
                "oddtown.kind": record["kind"],
                "oddtown.address": record["address"],
                "oddtown.record": json.dumps(record),
            },
        }

    async def _cids_for(self, agent_id: str) -> list[str]:
        request = search_v1.SearchCIDsRequest(
            queries=[search_v1.RecordQuery(type=QUERY.RECORD_QUERY_TYPE_NAME, value=self._name(agent_id))],
            limit=SEARCH_LIMIT,
        )
        return [r.record_cid for r in await self._call(lambda: self._client.search_cids(request))]

    async def register(self, record: Record) -> None:
        check_record(record)
        proto = core_v1.Record()
        ParseDict(self._to_oasf(record), proto.data)
        async with self._lock:
            old = await self._cids_for(record["id"])
            refs = await self._call(lambda: self._client.push([proto]))
            await self._publish(refs)
            stale = [cid for cid in old if cid not in {r.cid for r in refs}]
            if stale:
                await self._delete(stale)

    async def _publish(self, refs) -> None:
        # Announce on the Directory's routing layer. Search works without it,
        # so a routing hiccup must not block a birth.
        try:
            request = routing_v1.PublishRequest(record_refs=routing_v1.RecordRefs(refs=list(refs)))
            await self._call(lambda: self._client.publish(request))
        except Exception as exc:
            log.debug("routing publish failed: %s", exc)

    async def _delete(self, cids: list[str]) -> None:
        refs = [core_v1.RecordRef(cid=cid) for cid in cids]
        try:
            request = routing_v1.UnpublishRequest(record_refs=routing_v1.RecordRefs(refs=refs))
            await self._call(lambda: self._client.unpublish(request))
        except Exception as exc:
            log.debug("routing unpublish failed: %s", exc)
        for ref in refs:
            try:
                await self._call(lambda ref=ref: self._client.delete([ref]))
            except Exception as exc:
                # Two peers can notice the same death at once. Gone is gone.
                if "not found" not in str(exc).lower():
                    raise

    async def search(self, **match) -> list[Record]:
        request = search_v1.SearchRecordsRequest(
            queries=[
                search_v1.RecordQuery(type=QUERY.RECORD_QUERY_TYPE_ANNOTATION, value=f"oddtown.town:{self.town}")
            ],
            limit=SEARCH_LIMIT,
        )
        responses = await self._call(lambda: self._client.search_records(request))
        newest: dict[str, tuple[str, Record]] = {}
        for response in responses:
            data = MessageToDict(response.record.data, preserving_proto_field_name=True)
            annotations = data.get("annotations", {})
            if annotations.get("oddtown.town") != self.town:
                continue
            try:
                record = json.loads(annotations["oddtown.record"])
            except (KeyError, ValueError):
                continue
            created = data.get("created_at", "")
            # A replaced record can linger for a moment, so keep the newest per id.
            if record["id"] not in newest or created > newest[record["id"]][0]:
                newest[record["id"]] = (created, record)
        return sorted(
            (record for _, record in newest.values() if matches(record, match)),
            key=lambda r: r["id"],
        )

    async def remove(self, agent_id: str) -> None:
        async with self._lock:
            cids = await self._cids_for(agent_id)
            if cids:
                await self._delete(cids)

    async def close(self) -> None:
        self._client = None
