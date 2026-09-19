"""Packaged local collector. JSON-lines over private parent pipes; never a network listener."""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from dulwich.repo import Repo

from virtual_you.backend.collection import Collector
from virtual_you.contracts.activity import ActivityRecord, TimestampRange
from virtual_you.ingest.redact import assert_safe_serialized, redact_value


def collect(projects, data_dir):
    entries = []
    git_rows = {}
    for project in projects:
        root = Path(project["path"]).resolve(strict=True)
        if not root.is_dir() or not (root / ".git").exists():
            raise ValueError("Choose a Git project folder.")
        project_id = project["id"]
        git_rows[project_id] = []
        with Repo(str(root)) as repo:
            for item in repo.get_walker(max_entries=20):
                commit = item.commit
                moment = datetime.fromtimestamp(commit.commit_time, timezone.utc)
                message = commit.message.decode("utf-8", errors="replace")[:3000]
                record = ActivityRecord(
                    session_id=hashlib.sha256(project_id.encode() + commit.id).hexdigest(),
                    source="git",
                    end_state=redact_value("Committed " + commit.id.decode()[:12] + ": " + message),
                    timestamp_range=TimestampRange(started_at=moment, ended_at=moment),
                )
                git_rows[project_id].append(record.model_dump(mode="json"))
        # Discover only the exact selected project's editor directories, never all home history.
        slug = str(root).replace("/", "-")
        claude = Path.home() / ".claude" / "projects" / slug
        cursor = (
            Path.home()
            / ".cursor"
            / "projects"
            / str(root).lstrip("/").replace("/", "-")
            / "agent-transcripts"
        )
        for source, folder, pattern in [
            ("claude", claude, "*.jsonl"),
            ("cursor", cursor, "**/*.jsonl"),
        ]:
            if folder.is_dir():
                entries.append(
                    {
                        "source": source,
                        "path": str(folder),
                        "workspace": str(root),
                        "project": project_id,
                        "pattern": pattern,
                    }
                )
        codex = Path.home() / ".codex" / "sessions"
        if codex.is_dir():
            count = 0
            for candidate in codex.glob("*/*/*/*.jsonl"):
                count += 1
                if count > 2000:
                    break
                if candidate.is_symlink():
                    continue
                try:
                    with candidate.open() as stream:
                        first = stream.readline(16384)
                    metadata = json.loads(first)
                    cwd = metadata.get("payload", {}).get("cwd")
                    if (
                        metadata.get("type") == "session_meta"
                        and cwd
                        and Path(cwd).resolve() == root
                    ):
                        entries.append(
                            {
                                "source": "codex",
                                "path": str(candidate),
                                "workspace": str(root),
                                "project": project_id,
                            }
                        )
                except (OSError, ValueError):
                    continue
    entries = entries[:50]
    folder = Path(data_dir)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = folder / "sources.json"
    config.write_text(json.dumps(entries))
    config.chmod(0o600)
    settings = SimpleNamespace(
        ingestion_config=config, activity_dir=folder / "activities", data_dir=folder
    )
    result = Collector(settings).collect()
    batches = []
    for project in projects:
        rows = list(git_rows[project["id"]])
        for path in sorted((settings.activity_dir / project["id"]).glob("activity-*.json"))[-30:]:
            value = json.loads(path.read_text())
            value.update(source_path=None, diffs=[], prompts=[], tool_calls=[])
            # Raw patches, prompts and tool payloads remain local. Retain normalized summaries.
            for key in ("start_state", "reasoning_summary", "end_state"):
                value[key] = value[key][:6000]
            value["files_changed"] = value["files_changed"][:10]
            for change in value["files_changed"]:
                change["path"] = change["path"][-250:]
                if change.get("previous_path"):
                    change["previous_path"] = change["previous_path"][-250:]
            value = redact_value(value)
            assert_safe_serialized(value)
            rows.append(value)
        batches.append({"project": project["id"], "records": rows})
    return {"batches": batches, "errors": result["errors"]}


def main():
    for line in sys.stdin:
        try:
            if len(line) > 100000:
                raise ValueError()
            request = json.loads(line)
            if request.get("op") == "check":
                result = {"ready": True, "version": "0.3.0"}
            elif request.get("op") == "collect":
                result = collect(request["projects"], request["data_dir"])
            else:
                raise ValueError()
            print(json.dumps({"ok": True, "result": result}), flush=True)
        except Exception:
            print(
                json.dumps({"ok": False, "error": "collection_failed_check_project_access"}),
                flush=True,
            )


if __name__ == "__main__":
    main()
