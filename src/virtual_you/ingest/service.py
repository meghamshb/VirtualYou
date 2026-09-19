"""Public orchestration boundary for all Pathway 1 ingestion."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence, Union
from uuid import uuid4

from virtual_you.contracts.activity import ActivityRecord, SourceKind
from virtual_you.ingest.checkpoint import IncrementalJsonlReader
from virtual_you.ingest.claude import parse_claude_file, parse_claude_jsonl
from virtual_you.ingest.codex import parse_codex_file, parse_codex_jsonl_text
from virtual_you.ingest.cursor import (
    discover_cursor_source,
    parse_cursor_jsonl_text,
    parse_cursor_source,
)
from virtual_you.ingest.env_secrets import discover_env_secrets
from virtual_you.ingest.errors import IngestionError, IngestionErrorCode
from virtual_you.ingest.normalize import normalize_events
from virtual_you.ingest.redact import assert_safe_serialized, redact_value
from virtual_you.ingest.store import ActivityRecordRepository
from virtual_you.ingest.voice import VoiceTranscriptAdapter
from virtual_you.ingest.workspace import GIT_ROOT_ENV, overlay_git_state
from virtual_you.mcp.drive import DriveClient, RestDriveClient, drive_enabled, enrich_drive
from virtual_you.mcp.enrich import enrich, github_enabled
from virtual_you.mcp.github import GitHubClient, RestGitHubClient
from virtual_you.mcp.jira import JiraClient, RestJiraClient, enrich_jira, jira_enabled
from virtual_you.mcp.oauth import load_token
from virtual_you.mcp.observations import ObservationStore

PathLike = Union[str, Path]


class IngestionService:
    """Turn local raw activity into persisted, redacted activity records."""

    def __init__(
        self,
        repository: Optional[ActivityRecordRepository] = None,
        *,
        data_directory: Optional[PathLike] = None,
        now: Optional[Callable[[], datetime]] = None,
        env_search_root: Optional[PathLike] = None,
        env_secrets: Optional[Sequence[str]] = None,
        workspace_root: Optional[PathLike] = None,
        latest_work_only: bool = True,
        apply_git_overlay: bool = True,
        github_client: Optional[GitHubClient] = None,
        apply_github_enrichment: bool = True,
        jira_client: Optional[JiraClient] = None,
        drive_client: Optional[DriveClient] = None,
    ) -> None:
        configured_root = data_directory or os.environ.get("VIRTUAL_YOU_DATA_DIR")
        root = Path(configured_root or Path.home() / ".virtual-you")
        self._root = root
        self._latest_work_only = latest_work_only
        self._apply_git_overlay = apply_git_overlay
        self._repository = repository or ActivityRecordRepository(root / "activities")
        self._now = now or (lambda: datetime.now(timezone.utc))
        if workspace_root is not None:
            self._workspace_root = Path(workspace_root)
        else:
            configured = os.environ.get(GIT_ROOT_ENV, "").strip()
            self._workspace_root = Path(configured) if configured else Path.cwd()
        self._env_secrets = (
            tuple(secret for secret in env_secrets if secret)
            if env_secrets is not None
            else discover_env_secrets(env_search_root)
        )
        self._apply_github_enrichment = apply_github_enrichment
        self._github_client = github_client
        self._jira_client = jira_client
        self._drive_client = drive_client
        self._observation_store = ObservationStore(self._root)
        stored = load_token(self._root)
        secrets = list(self._env_secrets)
        if stored and stored not in secrets:
            secrets.append(stored)
        jira_token = (os.environ.get("JIRA_API_TOKEN") or "").strip()
        if jira_token and jira_token not in secrets:
            secrets.append(jira_token)
        drive_token = (os.environ.get("GOOGLE_ACCESS_TOKEN") or "").strip()
        if drive_token and drive_token not in secrets:
            secrets.append(drive_token)
        self._env_secrets = tuple(secrets)

    def ingest(
        self,
        *,
        path: PathLike,
        source: Union[str, SourceKind],
    ) -> ActivityRecord:
        return self.ingest_file(source, path)

    def ingest_file(
        self,
        source: Union[str, SourceKind],
        path: PathLike,
    ) -> ActivityRecord:
        source_kind = self._source_kind(source)
        source_path = Path(path).expanduser()
        if not source_path.exists():
            raise IngestionError(
                IngestionErrorCode.SOURCE_NOT_FOUND,
                "Activity source was not found.",
            )
        if source_kind == SourceKind.CURSOR:
            source_path = discover_cursor_source(source_path)

        if source_kind == SourceKind.CLAUDE:
            raw = normalize_events(
                parse_claude_file(source_path),
                source=source_kind.value,
                latest_work_only=self._latest_work_only,
                default_session_id=source_path.stem,
                fallback_timestamp=self._file_timestamp(source_path),
            )
        elif source_kind == SourceKind.CURSOR:
            raw = normalize_events(
                parse_cursor_source(source_path),
                source=source_kind.value,
                latest_work_only=self._latest_work_only,
                default_session_id=source_path.stem,
                fallback_timestamp=self._file_timestamp(source_path),
            )
        elif source_kind == SourceKind.CODEX:
            raw = normalize_events(
                parse_codex_file(source_path),
                source=source_kind.value,
                latest_work_only=self._latest_work_only,
                default_session_id=source_path.stem,
                fallback_timestamp=self._file_timestamp(source_path),
            )
        elif source_kind == SourceKind.VOICE:
            raw = self._voice_mapping(source_path.read_text(encoding="utf-8"))
        else:
            raise AssertionError("unreachable source kind")
        raw["source_path"] = str(source_path.resolve())
        return self._finalize(raw)

    def ingest_transcript(
        self,
        source: Union[str, SourceKind],
        text: str,
    ) -> ActivityRecord:
        source_kind = self._source_kind(source)
        if source_kind == SourceKind.CLAUDE:
            raw = normalize_events(
                parse_claude_jsonl(text),
                source=source_kind.value,
                latest_work_only=self._latest_work_only,
                fallback_timestamp=self._now(),
            )
        elif source_kind == SourceKind.CURSOR:
            raw = normalize_events(
                parse_cursor_jsonl_text(text),
                source=source_kind.value,
                latest_work_only=self._latest_work_only,
                fallback_timestamp=self._now(),
            )
        elif source_kind == SourceKind.CODEX:
            raw = normalize_events(
                parse_codex_jsonl_text(text),
                source=source_kind.value,
                latest_work_only=self._latest_work_only,
                fallback_timestamp=self._now(),
            )
        elif source_kind == SourceKind.VOICE:
            raw = self._voice_mapping(text)
        else:
            raise AssertionError("unreachable source kind")
        return self._finalize(raw)

    def watch(
        self,
        *,
        path: PathLike,
        source: Union[str, SourceKind],
    ) -> Optional[ActivityRecord]:
        """Process one incremental batch; callers may poll this operation."""

        source_kind = self._source_kind(source)
        if source_kind == SourceKind.VOICE:
            raise IngestionError(
                IngestionErrorCode.UNSUPPORTED_EVENT,
                "Voice transcripts are not append-only JSONL sources.",
            )
        source_path = Path(path).expanduser()
        if source_kind == SourceKind.CURSOR:
            source_path = discover_cursor_source(source_path)
        checkpoint_name = "{}-{}.json".format(
            source_kind.value,
            source_path.name.replace("/", "_"),
        )
        reader = IncrementalJsonlReader(
            source_path,
            self._root / "checkpoints" / checkpoint_name,
        )
        records = reader.read_available()
        if not records:
            return None
        text = "\n".join(
            json.dumps(record, ensure_ascii=False, default=str)
            for record in records
        )
        if source_kind == SourceKind.CLAUDE:
            events = parse_claude_jsonl(text)
        elif source_kind == SourceKind.CURSOR:
            events = parse_cursor_jsonl_text(text)
        elif source_kind == SourceKind.CODEX:
            events = parse_codex_jsonl_text(text)
        else:
            raise AssertionError("unreachable source kind")
        raw = normalize_events(
            events,
            source=source_kind.value,
                latest_work_only=self._latest_work_only,
            default_session_id=source_path.stem,
            fallback_timestamp=self._file_timestamp(source_path),
        )
        raw["source_path"] = str(source_path.resolve())
        return self._finalize(raw, merge_existing=True)

    def latest_activity(self) -> Optional[ActivityRecord]:
        return self._repository.latest_activity()

    def export(self, destination: Optional[PathLike] = None) -> str:
        return self._repository.export(destination)

    def _voice_mapping(self, text: str) -> dict:
        try:
            transcript = VoiceTranscriptAdapter().adapt(text)["text"]
        except ValueError as error:
            raise IngestionError(
                IngestionErrorCode.NOTHING_TO_REPORT,
                "Voice transcript contains nothing reportable.",
            ) from error
        timestamp = self._now()
        return {
            "session_id": "voice-{}".format(uuid4()),
            "source": SourceKind.VOICE.value,
            "start_state": transcript,
            "prompts": [transcript],
            "reasoning_summary": "",
            "files_changed": [],
            "diffs": [],
            "tool_calls": [],
            "end_state": transcript,
            "timestamp_range": {
                "started_at": timestamp,
                "ended_at": timestamp,
            },
        }

    def _finalize(
        self,
        raw: dict,
        *,
        merge_existing: bool = False,
    ) -> ActivityRecord:
        if self._apply_git_overlay:
            raw = overlay_git_state(raw, self._workspace_root)
        redacted = redact_value(raw, extra_secrets=self._env_secrets)
        redacted["schema_version"] = "1.0"
        redacted["redacted"] = True
        record = ActivityRecord.model_validate(redacted)
        if not record.has_reportable_evidence():
            raise IngestionError(
                IngestionErrorCode.NOTHING_TO_REPORT,
                "Activity source contains nothing reportable.",
            )
        if merge_existing:
            existing = self._repository.get(record.session_id)
            if existing is not None:
                record = self._merge_records(existing, record)
        if self._apply_github_enrichment and github_enabled():
            client = self._github_client
            if client is None:
                client = RestGitHubClient.from_env(
                    os.environ, data_directory=self._root
                )
            record = enrich(
                record,
                client=client,
                store=self._observation_store,
                extra_secrets=self._env_secrets,
            )
        if jira_enabled():
            jira = self._jira_client
            if jira is None:
                jira = RestJiraClient.from_env(os.environ)
            record = enrich_jira(
                record,
                client=jira,
                extra_secrets=self._env_secrets,
            )
        if drive_enabled():
            drive = self._drive_client
            if drive is None:
                drive = RestDriveClient.from_env(os.environ)
            record = enrich_drive(
                record,
                client=drive,
                extra_secrets=self._env_secrets,
            )
        assert_safe_serialized(record, extra_secrets=self._env_secrets)
        return self._repository.save(record)

    @staticmethod
    def _merge_records(
        existing: ActivityRecord,
        incoming: ActivityRecord,
    ) -> ActivityRecord:
        if existing.source != incoming.source:
            raise IngestionError(
                IngestionErrorCode.STORAGE_ERROR,
                "Cannot merge activity from different sources.",
            )

        files = list(existing.files_changed)
        file_keys = {
            (item.path, item.operation, item.previous_path)
            for item in files
        }
        for item in incoming.files_changed:
            key = (item.path, item.operation, item.previous_path)
            if key not in file_keys:
                files.append(item)
                file_keys.add(key)

        diffs = list(existing.diffs)
        for diff in incoming.diffs:
            if diff not in diffs:
                diffs.append(diff)

        tools = {item.call_id: item for item in existing.tool_calls}
        order = [item.call_id for item in existing.tool_calls]
        for item in incoming.tool_calls:
            previous = tools.get(item.call_id)
            if previous is None:
                order.append(item.call_id)
                tools[item.call_id] = item
                continue
            tools[item.call_id] = previous.model_copy(
                update={
                    "name": item.name if item.name != "unknown" else previous.name,
                    "input_summary": item.input_summary or previous.input_summary,
                    "result_summary": item.result_summary or previous.result_summary,
                    "status": (
                        item.status
                        if item.status != "requested"
                        else previous.status
                    ),
                    "timestamp": previous.timestamp or item.timestamp,
                }
            )

        reasoning = "\n".join(
            part
            for part in (
                existing.reasoning_summary,
                incoming.reasoning_summary,
            )
            if part
        )
        payload = existing.model_dump()
        payload.update(
            {
                "prompts": [*existing.prompts, *incoming.prompts],
                "reasoning_summary": reasoning,
                "files_changed": files,
                "diffs": diffs,
                "tool_calls": [tools[call_id] for call_id in order],
                "start_state": incoming.start_state or existing.start_state,
                "end_state": incoming.end_state or existing.end_state,
                "source_path": incoming.source_path or existing.source_path,
                "timestamp_range": {
                    "started_at": min(
                        existing.timestamp_range.started_at,
                        incoming.timestamp_range.started_at,
                    ),
                    "ended_at": max(
                        existing.timestamp_range.ended_at,
                        incoming.timestamp_range.ended_at,
                    ),
                },
            }
        )
        return ActivityRecord.model_validate(payload)

    @staticmethod
    def _file_timestamp(path: Path) -> datetime:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

    @staticmethod
    def _source_kind(source: Union[str, SourceKind]) -> SourceKind:
        try:
            return source if isinstance(source, SourceKind) else SourceKind(source)
        except ValueError as error:
            raise IngestionError(
                IngestionErrorCode.UNSUPPORTED_EVENT,
                "Unsupported activity source.",
            ) from error
