import os
from pathlib import Path

import pytest

from virtual_you.ingest.discover import discover_latest_session
from virtual_you.ingest.errors import IngestionError, IngestionErrorCode


def _write_jsonl(path: Path, mtime: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def test_discovers_newest_claude_session_and_skips_subagents(tmp_path: Path) -> None:
    projects = tmp_path / ".claude" / "projects" / "demo"
    older = _write_jsonl(projects / "older.jsonl", 1)
    newer = _write_jsonl(projects / "newer.jsonl", 5)
    _write_jsonl(projects / "subagents" / "noisy.jsonl", 9)

    found = discover_latest_session("claude", home=tmp_path)

    assert found == newer
    assert found != older


def test_prefers_claude_session_for_current_workspace(tmp_path: Path) -> None:
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    slug = str(cwd.resolve()).replace("/", "-")
    scoped = _write_jsonl(
        tmp_path / ".claude" / "projects" / slug / "current.jsonl",
        2,
    )
    _write_jsonl(
        tmp_path / ".claude" / "projects" / "other" / "newer-elsewhere.jsonl",
        9,
    )

    found = discover_latest_session("claude", home=tmp_path, cwd=cwd)

    assert found == scoped


def test_discovers_newest_cursor_transcript_for_workspace(tmp_path: Path) -> None:
    cwd = tmp_path / "Hackathon Project"
    cwd.mkdir()
    projects = tmp_path / "cursor-projects"
    slug = str(cwd.resolve()).lstrip("/").replace("/", "-").replace(" ", "-")
    transcripts = projects / slug / "agent-transcripts"
    older = _write_jsonl(transcripts / "aaaa.jsonl", 1)
    newer = _write_jsonl(transcripts / "bbbb.jsonl", 4)
    _write_jsonl(transcripts / "subagents" / "child.jsonl", 8)
    _write_jsonl(projects / "other" / "agent-transcripts" / "other.jsonl", 9)

    found = discover_latest_session(
        "cursor",
        cwd=cwd,
        cursor_projects=projects,
    )

    assert found == newer
    assert found != older


def test_cursor_falls_back_to_newest_project_transcript(tmp_path: Path) -> None:
    cwd = tmp_path / "empty-workspace"
    cwd.mkdir()
    projects = tmp_path / "cursor-projects"
    fallback = _write_jsonl(
        projects / "other" / "agent-transcripts" / "only.jsonl",
        3,
    )

    found = discover_latest_session(
        "cursor",
        cwd=cwd,
        cursor_projects=projects,
    )

    assert found == fallback


def test_discovers_newest_codex_rollout(tmp_path: Path) -> None:
    sessions = tmp_path / ".codex" / "sessions" / "2026" / "09" / "19"
    _write_jsonl(sessions / "notes.jsonl", 9)
    older = _write_jsonl(sessions / "rollout-older.jsonl", 1)
    newer = _write_jsonl(sessions / "rollout-newer.jsonl", 6)

    found = discover_latest_session("codex", home=tmp_path)

    assert found == newer
    assert found != older


def test_missing_source_tree_is_source_not_found(tmp_path: Path) -> None:
    with pytest.raises(IngestionError) as error:
        discover_latest_session("claude", home=tmp_path)

    assert error.value.code is IngestionErrorCode.SOURCE_NOT_FOUND


def test_voice_cannot_be_auto_discovered() -> None:
    with pytest.raises(IngestionError) as error:
        discover_latest_session("voice")

    assert error.value.code is IngestionErrorCode.UNSUPPORTED_EVENT
