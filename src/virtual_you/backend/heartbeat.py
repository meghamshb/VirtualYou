"""Refresh on startup and periodically; no generation, approval, or delivery here."""

from __future__ import annotations

import asyncio
import hashlib
import json

import httpx

from virtual_you.backend.collection import Collector
from virtual_you.backend.enrichment import RemoteEvidence
from virtual_you.backend.errors import ServiceError
from virtual_you.contracts.activity import ActivityRecord
from virtual_you.contracts.reporting import utcnow
from virtual_you.ingest.errors import IngestionError
from virtual_you.mcp.jira import JiraClient

MAX_RECORD_BYTES = 1_000_000
MAX_FEED_BYTES = 10_000_000


class Heartbeat:
    def __init__(self, settings, store, retrieval, client, jira_client=None, github_client=None):
        self.settings, self.store, self.retrieval, self.client = settings, store, retrieval, client
        self.jira_client: JiraClient | None = jira_client
        self.github_client = github_client
        self.remote_evidence = RemoteEvidence()
        self.lock = asyncio.Lock()
        self.fingerprints = {}
        self.collector = Collector(settings)

    async def refresh(self, *, force_integrations=False):
        started = utcnow()
        try:
            return await self._refresh(force_integrations=force_integrations)
        except Exception:
            # Manual/startup refresh failures must not leave an older healthy result visible.
            # Preserve the last successful timestamp, but never expose exception text or paths.
            previous = self.store.metadata("heartbeat") or {}
            status = {
                "collection": previous.get("collection", {}),
                "enabled": self.settings.heartbeat_enabled,
                "interval_seconds": self.settings.heartbeat_seconds,
                "started_at": started,
                "finished_at": utcnow(),
                "last_success_at": previous.get("last_success_at"),
                "changed": 0, "unchanged": 0, "removed": 0,
                "errors": [{"source": "collector", "code": "refresh_failed"}],
                "error_count": 1,
                "state": "failed",
                **self.retrieval.stats(),
            }
            self.store.set_metadata("heartbeat", status)
            return status

    async def _refresh(self, *, force_integrations=False):
        async with self.lock:
            started = utcnow()
            changed, skipped, removed, errors = 0, 0, 0, []
            observed = set()
            collection = await asyncio.to_thread(self.collector.collect)
            errors.extend(collection["errors"])
            remote = self.remote_evidence.begin(
                self.settings, self.collector.github_projects,
                github_client=self.github_client, jira_client=self.jira_client,
                force=force_integrations,
            )
            try:
                if not self.settings.activity_dir.is_dir():
                    raise OSError("Activity directory unavailable")
                paths = sorted(self.settings.activity_dir.glob("activity-*.json"))
                paths += sorted(self.settings.activity_dir.glob("*/activity-*.json"))
            except OSError:
                paths = []
                errors.append({"source": "local", "code": "directory_unavailable"})
            pending = []
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
                    payload = json.loads(raw)
                    # Spend the remote lookup budget on the newest work first.
                    ended = ActivityRecord.model_validate(payload).timestamp_range.ended_at
                    pending.append((ended.timestamp(), path, origin, digest, payload))
                except (OSError, ValueError, TypeError, KeyError):
                    self.fingerprints.pop(origin, None)
                    removed += self.retrieval.remove_origin(origin)
                    errors.append({"source": "local", "code": "invalid_activity_record"})
            for _, path, origin, digest, payload in sorted(pending, key=lambda item: item[0], reverse=True):
                try:
                    project = path.parent.name if path.parent != self.settings.activity_dir else None
                    # Even unchanged files must drop remote facts if a flag/scope changed.
                    payload = await asyncio.to_thread(remote.apply, payload, project)
                    updated = await asyncio.to_thread(self.retrieval.upsert, payload, origin)
                    changed += int(updated)
                    skipped += int(not updated)
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
                            # Feed records have no approved GitHub repository scope.
                            record = await asyncio.to_thread(remote.apply, record)
                            changed += int(await asyncio.to_thread(self.retrieval.upsert, record, "feed"))
                        except (ValueError, ServiceError, IngestionError):
                            errors.append({"source": "feed", "code": "invalid_activity_record"})
                except (httpx.HTTPError, ValueError):
                    errors.append({"source": "feed", "code": "feed_refresh_failed"})
            errors.extend(remote.errors)
            previous = self.store.metadata("heartbeat") or {}
            status = {
                "collection": collection,
                "enrichment": remote.summary(),
                "enabled": self.settings.heartbeat_enabled,
                "interval_seconds": self.settings.heartbeat_seconds,
                "started_at": started,
                "finished_at": utcnow(),
                "last_success_at": utcnow() if not errors else previous.get("last_success_at"),
                "changed": changed,
                "unchanged": skipped,
                "removed": removed,
                "errors": errors[:20],
                "error_count": len(errors) + max(
                    0, collection.get("error_count", 0) - len(collection["errors"])
                ),
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
