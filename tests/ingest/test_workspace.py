import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from virtual_you.ingest.service import IngestionService
from virtual_you.ingest.store import ActivityRecordRepository
from virtual_you.ingest.workspace import (
    MAX_DIFF_CHARS,
    overlay_git_state,
    read_workspace_snapshot,
)


PLANTED_SECRET = "sk-test-51Qx9ZaBcDeFgHiJkLmNoPqR"


def make_service(tmp_path: Path, workspace_root: Path) -> IngestionService:
    return IngestionService(
        ActivityRecordRepository(tmp_path / "activities"),
        data_directory=tmp_path,
        now=lambda: datetime(2026, 9, 19, 3, tzinfo=timezone.utc),
        env_search_root=tmp_path,
        workspace_root=workspace_root,
    )


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _run_git(path, "init")
    _run_git(path, "config", "user.email", "test@example.com")
    _run_git(path, "config", "user.name", "Test")
    _run_git(path, "config", "commit.gpgsign", "false")
    return path


def _commit_file(repo: Path, relative: str, contents: str, message: str) -> None:
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contents, encoding="utf-8")
    _run_git(repo, "add", relative)
    _run_git(repo, "commit", "-m", message)


def _session_with_prompt(path: Path, prompt: str = "Implement the change.") -> Path:
    path.write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": "git-session",
                "timestamp": "2026-09-19T01:00:00Z",
                "message": {"content": prompt},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_overlay_snapshots_dirty_and_untracked_files(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _commit_file(repo, "tracked.py", "print('ok')\n", "add tracked")
    (repo / "tracked.py").write_text("print('dirty')\n", encoding="utf-8")
    (repo / "new.py").write_text("print('added')\n", encoding="utf-8")

    snapshot = read_workspace_snapshot(repo)
    assert snapshot is not None
    assert snapshot.start_state.startswith("HEAD ")
    assert "add tracked" in snapshot.start_state
    assert "tracked.py @ HEAD" in snapshot.start_state
    assert "new.py untracked" in snapshot.start_state
    assert "tracked.py modified" in snapshot.end_state
    assert "new.py untracked" in snapshot.end_state

    by_path = {item.path: item for item in snapshot.files}
    assert by_path["tracked.py"].operation == "modified"
    assert by_path["new.py"].operation == "added"
    assert "print('dirty')" in by_path["tracked.py"].diff
    assert "print('added')" in by_path["new.py"].diff

    raw = {
        "start_state": "session prompt",
        "end_state": "session result",
        "files_changed": [
            {"path": "session.py", "operation": "modified", "previous_path": None}
        ],
        "diffs": ["session-only-diff"],
    }
    overlaid = overlay_git_state(raw, repo)
    assert overlaid["start_state"].startswith("HEAD ")
    assert "tracked.py modified" in overlaid["end_state"]
    assert "session-only-diff" not in overlaid["diffs"]
    assert any("print('dirty')" in diff for diff in overlaid["diffs"])
    assert {item["path"] for item in overlaid["files_changed"]} >= {
        "session.py",
        "tracked.py",
        "new.py",
    }


def test_overlay_skips_non_git_workspace(tmp_path: Path) -> None:
    raw = {
        "start_state": "keep me",
        "end_state": "also keep",
        "files_changed": [],
        "diffs": ["session-diff"],
    }
    assert overlay_git_state(raw, tmp_path) == raw


def test_ingest_overlay_uses_injected_workspace_root(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _commit_file(repo, "app.py", "value = 1\n", "initial app")
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    (repo / "extra.py").write_text("print('extra')\n", encoding="utf-8")
    session = _session_with_prompt(tmp_path / "session.jsonl")

    service = make_service(tmp_path, repo)
    record = service.ingest_file("claude", session)

    assert record.start_state.startswith("HEAD ")
    assert "app.py @ HEAD" in record.start_state
    assert "extra.py untracked" in record.start_state
    assert "app.py modified" in record.end_state
    assert "extra.py untracked" in record.end_state
    assert {item.path for item in record.files_changed} >= {"app.py", "extra.py"}
    joined = "\n".join(record.diffs)
    assert "value = 2" in joined
    assert "print('extra')" in joined


def test_planted_secret_in_dirty_file_never_persists(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _commit_file(repo, "app.py", "print('ok')\n", "initial")
    (repo / "app.py").write_text(
        "OPENAI_API_KEY={}\n".format(PLANTED_SECRET),
        encoding="utf-8",
    )
    (repo / "notes.txt").write_text(
        "token={}\n".format(PLANTED_SECRET),
        encoding="utf-8",
    )
    session = _session_with_prompt(tmp_path / "session.jsonl")
    service = make_service(tmp_path, repo)

    record = service.ingest_file("claude", session)
    serialized = record.model_dump_json()
    stored = next((tmp_path / "activities").glob("*.json")).read_text(encoding="utf-8")

    assert PLANTED_SECRET not in serialized
    assert PLANTED_SECRET not in service.export()
    assert PLANTED_SECRET not in stored
    assert "[REDACTED]" in serialized


def test_overlay_caps_large_diffs(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _commit_file(repo, "big.txt", "seed\n", "seed")
    (repo / "big.txt").write_text("x" * (MAX_DIFF_CHARS + 2000) + "\n", encoding="utf-8")

    snapshot = read_workspace_snapshot(repo)
    assert snapshot is not None
    assert snapshot.files[0].diff.endswith("\n... [truncated]")
    assert len(snapshot.files[0].diff) <= MAX_DIFF_CHARS


def test_make_service_does_not_scan_this_repo(tmp_path: Path) -> None:
    session = _session_with_prompt(tmp_path / "session.jsonl")
    service = make_service(tmp_path, tmp_path)
    record = service.ingest_file("claude", session)

    assert record.start_state == "Implement the change."
    assert record.files_changed == []
    assert record.diffs == []
