"""Explicit project-scoped raw sources → redacted ActivityRecords, off the DM path."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    # Explicit repository permission for this project; no global repository inference.
    github_repo: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    match_workspace: bool = False

    @model_validator(mode="after")
    def workspace_filter_source(self):
        if self.match_workspace and self.source != SourceKind.CODEX:
            raise ValueError("Workspace metadata filtering is supported for Codex sources only")
        return self


class Collector:
    def __init__(self, settings):
        self.settings = settings
        self.fingerprints = {}
        self.github_projects = {}

    def collect(self):
        self.github_projects = {}
        if not self.settings.ingestion_config:
            return {
                "enabled": False,
                "configured_sources": 0,
                "changed": 0,
                "unchanged": 0,
                "empty": 0,
                "errors": [],
                "error_count": 0,
                "sources": [],
            }
        errors, changed, unchanged, empty = [], 0, 0, 0
        try:
            entries = json.loads(self.settings.ingestion_config.read_text())
            if not isinstance(entries, list) or len(entries) > 50:
                raise ValueError()
            sources = [SourceConfig.model_validate(x) for x in entries]
        except (OSError, ValueError):
            return {
                "enabled": True,
                "configured_sources": 0,
                "changed": 0,
                "unchanged": 0,
                "empty": 0,
                "errors": [{"code": "invalid_ingestion_config"}],
                "error_count": 1,
                "sources": [],
            }
        project_repos = {}
        source_summaries = {}
        for source in sources:
            summary = source_summaries.setdefault(source.source.value, {
                "source": source.source.value, "configured": True,
                "scan_state": "empty", "changed": 0, "unchanged": 0,
                "error_count": 0, "filtered": 0,
            })
        for source in sources:
            before = (changed, unchanged, len(errors))
            try:
                root = Path(source.path).expanduser().resolve(strict=True)
                workspace = Path(source.workspace).expanduser().resolve(strict=True)
                if not workspace.is_dir():
                    raise ValueError()
                if Path(source.pattern).is_absolute() or ".." in Path(source.pattern).parts:
                    raise ValueError()
                repo = source.github_repo
                if not repo and os.getenv("VIRTUAL_YOU_GITHUB_REPO"):
                    remote = _run_git(workspace, "remote", "get-url", "origin")
                    found = github_remote_repo(remote.stdout) if not remote.returncode else None
                    configured = os.environ["VIRTUAL_YOU_GITHUB_REPO"].strip()
                    if found and found.casefold() == configured.casefold():
                        repo = configured
                if repo:
                    project_repos.setdefault(source.project, set()).add(repo.casefold())
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
                    else:
                        unchanged += 1
                    continue
                paths = sorted(root.glob(source.pattern)) if root.is_dir() else [root]
                if not paths:
                    empty += 1
                service = IngestionService(
                    repository=ActivityRecordRepository(
                        self.settings.data_dir / "ingestion-staging" / source.project
                    ),
                    data_directory=self.settings.data_dir,
                    env_search_root=workspace,
                    workspace_root=workspace,
                    latest_work_only=False,
                    apply_git_overlay=False,
                    # Runtime remote refresh belongs to the project-scoped heartbeat.
                    # Never mix the legacy global observation store into these records.
                    apply_github_enrichment=False,
                    apply_jira_enrichment=False,
                )
                for path in paths:
                    try:
                        if path.is_symlink() or not path.is_file():
                            continue
                        if root.is_dir() and not path.resolve().is_relative_to(root):
                            continue
                        if source.match_workspace and not codex_workspace_matches(path, workspace):
                            source_summaries[source.source.value]["filtered"] += 1
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
                            unchanged += 1
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
                        try:
                            record = service.ingest_file(source.source, path)
                        except IngestionError as error:
                            if error.code == IngestionErrorCode.NOTHING_TO_REPORT:
                                empty += 1
                                self.fingerprints[key] = fingerprint
                                continue
                            raise
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
            finally:
                summary = source_summaries[source.source.value]
                summary["changed"] += changed - before[0]
                summary["unchanged"] += unchanged - before[1]
                summary["error_count"] += len(errors) - before[2]
        for project, repos in project_repos.items():
            if len(repos) == 1:
                self.github_projects[project] = next(iter(repos))
            else:
                errors.append({"source": "github", "code": "github_scope_conflict"})
        for summary in source_summaries.values():
            summary["scan_state"] = (
                "failed" if summary["error_count"] else
                "healthy" if summary["changed"] or summary["unchanged"] else "empty"
            )
        return {
            "enabled": True,
            "configured_sources": len(sources),
            "changed": changed,
            "unchanged": unchanged,
            "empty": empty,
            "errors": errors[:20],
            "error_count": len(errors),
            "sources": list(source_summaries.values()),
        }


def github_remote_repo(remote: str) -> str | None:
    """Parse an origin locally; credentials and non-GitHub hosts never become evidence."""
    remote = remote.strip()
    if remote.startswith("git@github.com:"):
        path = remote[len("git@github.com:"):]
    else:
        parsed = urlparse(remote)
        if parsed.scheme not in {"https", "ssh"} or parsed.hostname != "github.com":
            return None
        path = parsed.path.lstrip("/")
    path = path.removesuffix(".git").rstrip("/")
    return path if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", path) else None


def codex_workspace_matches(path: Path, workspace: Path) -> bool:
    """Read only bounded first-line session metadata before opening public session chunks."""
    with path.open("rb") as stream:
        first = stream.readline(1_000_001)
    if len(first) > 1_000_000:
        raise ValueError("Codex session metadata is too large")
    metadata = json.loads(first)
    if not isinstance(metadata, dict) or metadata.get("type") != "session_meta":
        raise ValueError("Codex session metadata is missing")
    payload = metadata.get("payload")
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise ValueError("Codex session workspace is missing")
    return Path(cwd).resolve() == workspace
