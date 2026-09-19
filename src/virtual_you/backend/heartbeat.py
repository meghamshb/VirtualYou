"""Refresh on startup and periodically; no generation, approval, or delivery here."""

from __future__ import annotations

import asyncio
import hashlib
import json

import httpx

from virtual_you.backend.collection import Collector
from virtual_you.backend.errors import ServiceError
from virtual_you.contracts.reporting import utcnow
from virtual_you.ingest.errors import IngestionError

MAX_RECORD_BYTES = 1_000_000
MAX_FEED_BYTES = 10_000_000


class Heartbeat:
    def __init__(self, settings, store, retrieval, client):
        self.settings, self.store, self.retrieval, self.client = settings, store, retrieval, client
        self.lock = asyncio.Lock()
        self.fingerprints = {}
        self.collector = Collector(settings)

    async def refresh(self):
        async with self.lock:
            started = utcnow()
            changed, skipped, removed, errors = 0, 0, 0, []
            observed = set()
            collection = await asyncio.to_thread(self.collector.collect)
            errors.extend(collection["errors"])
            try:
                if not self.settings.activity_dir.is_dir():
                    raise OSError("Activity directory unavailable")
                paths = sorted(self.settings.activity_dir.glob("activity-*.json"))
                paths += sorted(self.settings.activity_dir.glob("*/activity-*.json"))
            except OSError:
                paths = []
                errors.append({"source": "local", "code": "directory_unavailable"})
            for path in paths:
                origin = "file:" + str(path)
                observed.add(origin)
                try:
                    if (
                        path.is_symlink()
                        or path.parent.is_symlink()
                        or path.stat().st_size > MAX_RECORD_BYTES
                    ):
                        raise ValueError("Invalid activity file")
                    raw = await asyncio.to_thread(path.read_bytes)
                    digest = hashlib.sha256(raw).hexdigest()
                    if self.fingerprints.get(origin) == digest:
                        skipped += 1
                        continue
                    payload = json.loads(raw)
                    changed += int(await asyncio.to_thread(self.retrieval.upsert, payload, origin))
                    if path.parent != self.settings.activity_dir:
                        self.retrieval.assign_project([payload["session_id"]], path.parent.name)
                    self.fingerprints[origin] = digest
                except (OSError, ValueError, ServiceError, IngestionError):
                    # Do not show filenames, raw validation errors, or record contents.
                    self.fingerprints.pop(origin, None)
                    removed += self.retrieval.remove_origin(origin)
                    errors.append({"source": "local", "code": "invalid_activity_record"})
            if not any(e["code"] == "directory_unavailable" for e in errors):
                for origin in self.retrieval.origins():
                    if origin.startswith("file:") and origin not in observed:
                        removed += self.retrieval.remove_origin(origin)
                        self.fingerprints.pop(origin, None)
            if self.settings.activity_feed_url:
                try:
                    headers = {}
                    if self.settings.activity_feed_token:
                        headers["Authorization"] = "Bearer " + self.settings.activity_feed_token
                    async with self.client.stream(
                        "GET",
                        self.settings.activity_feed_url,
                        headers=headers,
                        follow_redirects=False,
                    ) as response:
                        response.raise_for_status()
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > MAX_FEED_BYTES:
                                raise ValueError("Feed too large")
                    records = json.loads(data)
                    if not isinstance(records, list) or len(records) > 1000:
                        raise ValueError("Expected an array of normalized ActivityRecords")
                    for record in records:
                        try:
                            if len(json.dumps(record)) > MAX_RECORD_BYTES:
                                raise ValueError("Record too large")
                            changed += int(await asyncio.to_thread(self.retrieval.upsert, record, "feed"))
                        except (ValueError, ServiceError, IngestionError):
                            errors.append({"source": "feed", "code": "invalid_activity_record"})
                except (httpx.HTTPError, ValueError):
                    errors.append({"source": "feed", "code": "feed_refresh_failed"})
            previous = self.store.metadata("heartbeat") or {}
            status = {
                "collection": collection,
                "enabled": self.settings.heartbeat_enabled,
                "interval_seconds": self.settings.heartbeat_seconds,
                "started_at": started,
                "finished_at": utcnow(),
                "last_success_at": utcnow() if not errors else previous.get("last_success_at"),
                "changed": changed,
                "unchanged": skipped,
                "removed": removed,
                "errors": errors[:20],
                "error_count": len(errors),
                "state": "degraded" if errors else "healthy",
                **self.retrieval.stats(),
            }
            self.store.set_metadata("heartbeat", status)
            return status

    async def run(self):
        while True:
            try:
                await self.refresh()
            except Exception:
                self.store.set_metadata(
                    "heartbeat",
                    {
                        "state": "failed",
                        "finished_at": utcnow(),
                        "errors": [{"code": "refresh_failed"}],
                    },
                )
            await asyncio.sleep(self.settings.heartbeat_seconds)
