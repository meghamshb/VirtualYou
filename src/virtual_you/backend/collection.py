"""Explicit project-scoped raw sources → redacted ActivityRecords, off the DM path."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from virtual_you.contracts.activity import SourceKind
from virtual_you.ingest.codex import public_session_chunks
from virtual_you.ingest.errors import IngestionError, IngestionErrorCode
from virtual_you.ingest.git import collect_commits
from virtual_you.ingest.service import IngestionService
from virtual_you.ingest.store import ActivityRecordRepository
from virtual_you.ingest.workspace import _run_git


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: SourceKind
    path: str
    project: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    workspace: str
    # Directory patterns are relative to the explicitly approved source directory.
    pattern: str = "*.jsonl"


class Collector:
    def __init__(self, settings):
        self.settings = settings
        self.fingerprints = {}

    def collect(self):
        if not self.settings.ingestion_config:
            return {"enabled": False, "changed": 0, "errors": []}
        errors, changed = [], 0
        try:
            entries = json.loads(self.settings.ingestion_config.read_text())
            if not isinstance(entries, list) or len(entries) > 50:
                raise ValueError()
            sources = [SourceConfig.model_validate(x) for x in entries]
        except (OSError, ValueError):
            return {"enabled": True, "changed": 0, "errors": [{"code": "invalid_ingestion_config"}]}
        for source in sources:
            try:
                root = Path(source.path).expanduser().resolve(strict=True)
                workspace = Path(source.workspace).expanduser().resolve(strict=True)
                if not workspace.is_dir():
                    raise ValueError()
                if Path(source.pattern).is_absolute() or ".." in Path(source.pattern).parts:
                    raise ValueError()
                if source.source == SourceKind.GIT:
                    # Skip unchanged commits before expensive diff extraction/redaction.
                    head = _run_git(workspace, "rev-parse", "HEAD")
                    if head.returncode:
                        raise ValueError()
                    key = (source.project, "git", str(workspace))
                    if self.fingerprints.get(key) != head.stdout:
                        for record in collect_commits(workspace):
                            record.session_id = hashlib.sha256(
                                (source.project + record.session_id).encode()
                            ).hexdigest()
                            ActivityRecordRepository(
                                self.settings.activity_dir / source.project
                            ).save(record)
                            changed += 1
                        self.fingerprints[key] = head.stdout
                    continue
                paths = sorted(root.glob(source.pattern)) if root.is_dir() else [root]
                if not paths:
                    errors.append({"source": source.source.value, "code": "no_source_files"})
                service = IngestionService(
                    repository=ActivityRecordRepository(
                        self.settings.data_dir / "ingestion-staging" / source.project
                    ),
                    data_directory=self.settings.data_dir,
                    env_search_root=workspace,
                    workspace_root=workspace,
                    latest_work_only=False,
                    apply_git_overlay=False,
                )
                for path in paths:
                    try:
                        if path.is_symlink() or not path.is_file():
                            continue
                        if root.is_dir() and not path.resolve().is_relative_to(root):
                            continue
                        stat = path.stat()
                        # Codex streams bounded public chunks and skips embedded images.
                        # Long interactive sessions commonly exceed the whole-file
                        # parsers' 50 MB limit; keep a separate bounded allowance.
                        size_limit = (
                            500_000_000 if source.source == SourceKind.CODEX else 50_000_000
                        )
                        if stat.st_size > size_limit:
                            raise ValueError()
                        # SQLite WAL changes also invalidate the source fingerprint.
                        wal = Path(str(path) + "-wal")
                        fingerprint = (
                            stat.st_mtime_ns,
                            stat.st_size,
                            wal.stat().st_mtime_ns if wal.exists() else 0,
                        )
                        key = (source.project, source.source.value, str(path))
                        if self.fingerprints.get(key) == fingerprint:
                            continue
                        if source.source == SourceKind.CODEX:
                            for index, chunk in enumerate(public_session_chunks(path)):
                                digest = hashlib.sha256(
                                    json.dumps(chunk, sort_keys=True).encode()
                                ).hexdigest()
                                chunk_key = (*key, index)
                                if self.fingerprints.get(chunk_key) == digest:
                                    continue
                                try:
                                    record = service.ingest_transcript(
                                        "codex", "\n".join(json.dumps(x) for x in chunk)
                                    )
                                except IngestionError as error:
                                    if error.code == IngestionErrorCode.NOTHING_TO_REPORT:
                                        continue
                                    raise
                                record.session_id = hashlib.sha256(
                                    (
                                        source.project + ":codex:" + str(path) + ":" + str(index)
                                    ).encode()
                                ).hexdigest()
                                # Only normalized and redacted records leave ingestion.
                                ActivityRecordRepository(
                                    self.settings.activity_dir / source.project
                                ).save(record)
                                self.fingerprints[chunk_key] = digest
                                changed += 1
                            self.fingerprints[key] = fingerprint
                            continue
                        record = service.ingest_file(source.source, path)
                        # Ingestion IDs may collide across projects/files. Namespace before
                        # publication; never allow one project's log to overwrite another.
                        record.session_id = hashlib.sha256(
                            (
                                source.project
                                + ":"
                                + source.source.value
                                + ":"
                                + str(path)
                                + ":"
                                + record.session_id
                            ).encode()
                        ).hexdigest()
                        ActivityRecordRepository(self.settings.activity_dir / source.project).save(
                            record
                        )
                        self.fingerprints[key] = fingerprint
                        changed += 1
                    except Exception:
                        errors.append(
                            {"source": source.source.value, "code": "source_ingestion_failed"}
                        )
            except (OSError, ValueError, IngestionError):
                errors.append({"source": source.source.value, "code": "source_unavailable"})
        return {"enabled": True, "changed": changed, "errors": errors[:20]}
