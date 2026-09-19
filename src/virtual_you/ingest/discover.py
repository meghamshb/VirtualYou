"""Locate the newest local Claude, Cursor, or Codex session file."""

import os
from pathlib import Path
from typing import NoReturn, Optional, Tuple, Union

from virtual_you.contracts.activity import SourceKind
from virtual_you.ingest.errors import IngestionError, IngestionErrorCode


PathLike = Union[str, Path]
_SKIP_TRANSCRIPT_PARTS = ("subagents",)


def discover_latest_session(
    source: Union[str, SourceKind],
    *,
    home: Optional[PathLike] = None,
    cwd: Optional[PathLike] = None,
    claude_projects: Optional[PathLike] = None,
    cursor_projects: Optional[PathLike] = None,
    codex_sessions: Optional[PathLike] = None,
) -> Path:
    """Return the newest ingestible file for *source* on this machine."""

    kind = _source_kind(source)
    resolved_home = Path(home).expanduser() if home is not None else None
    resolved_cwd = Path(cwd).expanduser() if cwd is not None else Path.cwd()
    if kind == SourceKind.CLAUDE:
        return discover_latest_claude(
            home=resolved_home,
            cwd=resolved_cwd,
            projects_root=claude_projects,
        )
    if kind == SourceKind.CURSOR:
        return discover_latest_cursor(
            home=resolved_home,
            cwd=resolved_cwd,
            projects_root=cursor_projects,
        )
    if kind == SourceKind.CODEX:
        return discover_latest_codex(
            home=resolved_home,
            sessions_root=codex_sessions,
        )
    if kind == SourceKind.VOICE:
        raise IngestionError(
            IngestionErrorCode.UNSUPPORTED_EVENT,
            "Voice transcripts cannot be auto-discovered; pass a path.",
        )
    _assert_never(kind.value)


def discover_latest_claude(
    *,
    home: Optional[PathLike] = None,
    cwd: Optional[PathLike] = None,
    projects_root: Optional[PathLike] = None,
) -> Path:
    root = _configured_root(
        explicit=projects_root,
        env_name="VIRTUAL_YOU_CLAUDE_PROJECTS",
        home=home,
        relative=Path(".claude") / "projects",
    )
    workspace = Path(cwd).expanduser() if cwd is not None else Path.cwd()
    scoped = root / _claude_project_slug(workspace)
    if scoped.is_dir():
        try:
            return _newest_jsonl(scoped)
        except IngestionError:
            pass
    return _newest_jsonl(root)


def discover_latest_cursor(
    *,
    home: Optional[PathLike] = None,
    cwd: Optional[PathLike] = None,
    projects_root: Optional[PathLike] = None,
) -> Path:
    root = _configured_root(
        explicit=projects_root,
        env_name="VIRTUAL_YOU_CURSOR_PROJECTS",
        home=home,
        relative=Path(".cursor") / "projects",
    )
    workspace = Path(cwd).expanduser() if cwd is not None else Path.cwd()
    scoped = root / _cursor_project_slug(workspace) / "agent-transcripts"
    if scoped.is_dir():
        try:
            return _newest_jsonl(scoped)
        except IngestionError:
            pass
    return _newest_jsonl(root)


def discover_latest_codex(
    *,
    home: Optional[PathLike] = None,
    sessions_root: Optional[PathLike] = None,
) -> Path:
    root = _configured_root(
        explicit=sessions_root,
        env_name="VIRTUAL_YOU_CODEX_SESSIONS",
        home=home,
        relative=Path(".codex") / "sessions",
    )
    return _newest_jsonl(root, name_prefix="rollout-")


def _configured_root(
    *,
    explicit: Optional[PathLike],
    env_name: str,
    home: Optional[PathLike],
    relative: Path,
) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser()
    if home is not None:
        return Path(home).expanduser() / relative
    configured = os.environ.get(env_name)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / relative


def _newest_jsonl(
    root: Path,
    *,
    name_prefix: str = "",
    skip_parts: Tuple[str, ...] = _SKIP_TRANSCRIPT_PARTS,
) -> Path:
    if not root.exists():
        raise IngestionError(
            IngestionErrorCode.SOURCE_NOT_FOUND,
            "Activity source was not found.",
        )

    newest: Optional[Path] = None
    newest_mtime: Optional[float] = None
    newest_key: Optional[str] = None
    for path in root.rglob("*.jsonl"):
        if not path.is_file():
            continue
        if name_prefix and not path.name.startswith(name_prefix):
            continue
        if any(part in skip_parts for part in path.parts):
            continue
        stat = path.stat()
        key = str(path)
        if (
            newest is None
            or stat.st_mtime > newest_mtime
            or (stat.st_mtime == newest_mtime and key > newest_key)
        ):
            newest = path
            newest_mtime = stat.st_mtime
            newest_key = key

    if newest is None:
        raise IngestionError(
            IngestionErrorCode.SOURCE_NOT_FOUND,
            "Activity source was not found.",
        )
    return newest


def _claude_project_slug(cwd: Path) -> str:
    return str(cwd.resolve()).replace("/", "-")


def _cursor_project_slug(cwd: Path) -> str:
    return str(cwd.resolve()).lstrip("/").replace("/", "-").replace(" ", "-")


def _source_kind(source: Union[str, SourceKind]) -> SourceKind:
    if isinstance(source, SourceKind):
        return source
    try:
        return SourceKind(source)
    except ValueError as exc:
        raise IngestionError(
            IngestionErrorCode.UNSUPPORTED_EVENT,
            "Unsupported activity source.",
        ) from exc


def _assert_never(value: str) -> NoReturn:
    raise AssertionError("unhandled source: {}".format(value))
