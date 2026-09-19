"""Read-only Drive: one folder, title + URL only. No file bytes, no search."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Protocol, Sequence
from urllib.parse import urlencode

from virtual_you.contracts.activity import ActivityRecord, ToolCall
from virtual_you.ingest.redact import assert_safe_serialized, redact_value

try:
    import httpx
except ImportError:
    httpx = None

DRIVE_FLAG_ENV = "VIRTUAL_YOU_MCP_DRIVE"
DRIVE_FOLDER_ENV = "VIRTUAL_YOU_DRIVE_FOLDER_ID"
DRIVE_TOKEN_ENV = "GOOGLE_ACCESS_TOKEN"
TRUE_VALUES = {"1", "true", "yes", "on"}
DEFAULT_TIMEOUT = 10.0
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
FILE_FIELDS = "files(id,name,mimeType,webViewLink,modifiedTime)"
MAX_DRIVE_FILES = 5


@dataclass(frozen=True)
class DriveFile:
    file_id: str
    name: str
    mime_type: str
    web_view_link: str
    modified_time: str = ""


class DriveClient(Protocol):
    def list_folder(self, started_at: str, ended_at: str) -> Sequence[DriveFile]:
        """Files in the configured folder modified in [started_at, ended_at]."""


class FakeDriveClient:
    """In-memory client for tests. Does not perform HTTP."""

    def __init__(
        self,
        files: Optional[Sequence[DriveFile]] = None,
        *,
        contents: Optional[dict] = None,
    ) -> None:
        self.files = list(files or [])
        self.contents = dict(contents or {})
        self.calls: List[tuple] = []

    def list_folder(self, started_at: str, ended_at: str) -> Sequence[DriveFile]:
        self.calls.append((started_at, ended_at))
        start = _parse_time(started_at)
        end = _parse_time(ended_at)
        matched = []
        for item in self.files:
            modified = _parse_time(item.modified_time)
            if start is not None and end is not None and modified is not None:
                if modified < start or modified > end:
                    continue
            matched.append(item)
            if len(matched) >= MAX_DRIVE_FILES:
                break
        return matched


class RestDriveClient:
    """Read-only Drive v3. Lists one folder. Never reads file bytes."""

    def __init__(
        self,
        folder_id: str,
        token: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        http_get=None,
    ) -> None:
        self.folder_id = folder_id
        self.token = token
        self.timeout = timeout
        self._http_get = http_get

    @classmethod
    def from_env(cls, environ) -> Optional["RestDriveClient"]:
        folder_id = (environ.get(DRIVE_FOLDER_ENV) or "").strip()
        token = (environ.get(DRIVE_TOKEN_ENV) or "").strip()
        if not folder_id or not token:
            return None
        return cls(folder_id, token)

    def list_folder(self, started_at: str, ended_at: str) -> Sequence[DriveFile]:
        if not self.folder_id or not started_at or not ended_at:
            return []
        query = (
            "'{}' in parents and trashed = false and "
            "modifiedTime >= '{}' and modifiedTime <= '{}'"
        ).format(self.folder_id, started_at, ended_at)
        payload = self._get(
            DRIVE_FILES_URL
            + "?"
            + urlencode(
                {
                    "q": query,
                    "fields": FILE_FIELDS,
                    "pageSize": str(MAX_DRIVE_FILES),
                    "corpora": "user",
                }
            )
        )
        if not isinstance(payload, dict):
            return []
        raw_files = payload.get("files") or []
        if not isinstance(raw_files, list):
            return []
        files = []
        for item in raw_files:
            mapped = _map_file(item)
            if mapped is None:
                continue
            files.append(mapped)
            if len(files) >= MAX_DRIVE_FILES:
                break
        return files

    def _get(self, url: str):
        getter = self._http_get
        if getter is None:
            getter = _httpx_get
        return getter(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer {}".format(self.token),
            },
            timeout=self.timeout,
        )


def drive_enabled(environ=None) -> bool:
    env = environ if environ is not None else os.environ
    return (env.get(DRIVE_FLAG_ENV) or "").strip().lower() in TRUE_VALUES


def enrich_drive(
    record: ActivityRecord,
    *,
    client: Optional[DriveClient] = None,
    extra_secrets: Optional[Sequence[str]] = None,
    enabled: Optional[bool] = None,
) -> ActivityRecord:
    """No-op when the flag is off. Never stores file bytes. Never raises."""

    if enabled is None:
        enabled = drive_enabled()
    if not enabled:
        return record
    secrets = extra_secrets if extra_secrets is not None else ()
    if client is None:
        return record

    started = _iso(record.timestamp_range.started_at)
    ended = _iso(record.timestamp_range.ended_at)
    try:
        files = list(client.list_folder(started, ended) or [])
    except Exception:
        files = []

    calls = list(record.tool_calls)
    existing = {call.call_id for call in calls}
    for item in files[:MAX_DRIVE_FILES]:
        if not item.web_view_link:
            continue
        call_id = "drive.file:{}".format(item.file_id or item.name)
        if call_id in existing:
            continue
        summary = "{} {} {}".format(item.web_view_link, item.name, item.mime_type).strip()
        if item.modified_time:
            summary = "{} modified {}".format(summary, item.modified_time[:10])
        calls.append(
            ToolCall(
                call_id=call_id,
                name="drive.file",
                input_summary=item.name,
                result_summary=summary[:1500],
                status="succeeded",
            )
        )
        existing.add(call_id)

    payload = record.model_dump()
    payload["tool_calls"] = [call.model_dump() for call in calls]
    redacted = redact_value(payload, extra_secrets=secrets)
    redacted["schema_version"] = "1.0"
    redacted["redacted"] = True
    enriched = ActivityRecord.model_validate(redacted)
    assert_safe_serialized(enriched, extra_secrets=secrets)
    return enriched


def _map_file(item) -> Optional[DriveFile]:
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or "").strip()
    link = str(item.get("webViewLink") or "").strip()
    file_id = str(item.get("id") or "").strip()
    if not name or not link:
        return None
    return DriveFile(
        file_id=file_id or name,
        name=name,
        mime_type=str(item.get("mimeType") or "").strip(),
        web_view_link=link,
        modified_time=str(item.get("modifiedTime") or "").strip(),
    )


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value: str) -> Optional[datetime]:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _httpx_get(url: str, headers: dict, timeout: float):
    if httpx is None:
        return None
    try:
        response = httpx.get(url, headers=headers, timeout=timeout)
        if response.status_code in (401, 403, 404):
            return None
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError:
        return None
