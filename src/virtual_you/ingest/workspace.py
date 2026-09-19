"""Read-only git overlay of local uncommitted and untracked work."""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


PathLike = Union[str, Path]

GIT_ROOT_ENV = "VIRTUAL_YOU_GIT_ROOT"
MAX_OVERLAY_FILES = 32
MAX_DIFF_CHARS = 4000
DIFF_TRUNCATION = "\n... [truncated]"

_SKIP_PREFIXES = (
    ".virtual-you/",
    ".venv/",
    "venv/",
    "node_modules/",
    "__pycache__/",
)
_SKIP_NAMES = {
    ".virtual-you",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
}
_SEARCH_DEPTH = 8
_GIT_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class DirtyPath:
    path: str
    operation: str
    previous_path: Optional[str]
    in_head: bool
    end_label: str
    diff: str


@dataclass(frozen=True)
class WorkspaceSnapshot:
    short_sha: str
    subject: str
    files: Tuple[DirtyPath, ...]

    @property
    def start_state(self) -> str:
        lines = ["HEAD {} {}".format(self.short_sha, self.subject).rstrip()]
        for item in self.files:
            if item.in_head:
                lines.append("{} @ HEAD".format(item.path))
            else:
                lines.append("{} untracked".format(item.path))
        return "\n".join(lines)

    @property
    def end_state(self) -> str:
        return "\n".join(
            "{} {}".format(item.path, item.end_label) for item in self.files
        )


def overlay_git_state(
    raw: Dict[str, Any],
    workspace_root: Optional[PathLike] = None,
) -> Dict[str, Any]:
    """Replace session diffs with HEAD→working-tree git diffs when available."""

    snapshot = read_workspace_snapshot(workspace_root)
    if snapshot is None or not snapshot.files:
        return raw

    files = list(raw.get("files_changed") or [])
    seen = {
        (item.get("path"), item.get("operation"), item.get("previous_path"))
        for item in files
        if isinstance(item, dict)
    }
    for item in snapshot.files:
        key = (item.path, item.operation, item.previous_path)
        if key in seen:
            continue
        files.append(
            {
                "path": item.path,
                "operation": item.operation,
                "previous_path": item.previous_path,
            }
        )
        seen.add(key)

    updated = dict(raw)
    updated["files_changed"] = files
    updated["diffs"] = [item.diff for item in snapshot.files if item.diff]
    updated["start_state"] = snapshot.start_state
    updated["end_state"] = snapshot.end_state
    return updated


def read_workspace_snapshot(
    workspace_root: Optional[PathLike] = None,
) -> Optional[WorkspaceSnapshot]:
    root = resolve_git_root(workspace_root)
    if root is None:
        return None
    short = _run_git(root, "rev-parse", "--short", "HEAD")
    if short.returncode != 0 or not short.stdout.strip():
        return None
    subject = _run_git(root, "log", "-1", "--format=%s")
    status = _run_git(root, "status", "--porcelain=v1", "-uall")
    if status.returncode != 0:
        return None

    files: List[DirtyPath] = []
    for raw_line in status.stdout.splitlines():
        parsed = _parse_porcelain_line(raw_line)
        if parsed is None:
            continue
        xy, path, previous_path = parsed
        if _should_skip(path):
            continue
        operation, in_head, end_label = _classify_status(xy)
        diff = _diff_for_path(root, path, untracked=xy == "??")
        files.append(
            DirtyPath(
                path=path,
                operation=operation,
                previous_path=previous_path,
                in_head=in_head,
                end_label=end_label,
                diff=diff,
            )
        )
        if len(files) >= MAX_OVERLAY_FILES:
            break

    return WorkspaceSnapshot(
        short_sha=short.stdout.strip(),
        subject=subject.stdout.strip(),
        files=tuple(files),
    )


def resolve_git_root(start: Optional[PathLike] = None) -> Optional[Path]:
    return _search_git_root(Path(start or Path.cwd()))


def _search_git_root(start: Path) -> Optional[Path]:
    current = start.expanduser()
    try:
        current = current.resolve()
    except OSError:
        current = start.expanduser()
    for _ in range(_SEARCH_DEPTH):
        found = _git_dir(current)
        if found is not None:
            return found
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def _git_dir(path: Path) -> Optional[Path]:
    candidate = path.expanduser()
    try:
        candidate = candidate.resolve()
    except OSError:
        return None
    if (candidate / ".git").exists():
        return candidate
    return None


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["GIT_PAGER"] = "cat"
    env["GIT_OPTIONAL_LOCKS"] = "1"
    env["LC_ALL"] = "C"
    try:
        return subprocess.run(
            ["git", "-c", "color.ui=never", "-c", "core.quotepath=false", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(
            args=["git", *args],
            returncode=1,
            stdout="",
            stderr="",
        )


def _parse_porcelain_line(
    line: str,
) -> Optional[Tuple[str, str, Optional[str]]]:
    if len(line) < 4:
        return None
    xy = line[:2]
    remainder = line[3:]
    previous_path = None
    if " -> " in remainder:
        source, _, destination = remainder.partition(" -> ")
        previous_path = _unquote_path(source)
        remainder = destination
    path = _unquote_path(remainder)
    if not path:
        return None
    return xy, path, previous_path


def _unquote_path(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        try:
            return bytes(text[1:-1], "utf-8").decode("unicode_escape")
        except (UnicodeDecodeError, ValueError):
            return text[1:-1]
    return text


def _should_skip(path: str) -> bool:
    posix = path.replace("\\", "/").lstrip("./")
    if posix in _SKIP_NAMES:
        return True
    for prefix in _SKIP_PREFIXES:
        if posix.startswith(prefix):
            return True
    parts = posix.split("/")
    return any(part in _SKIP_NAMES for part in parts)


def _classify_status(xy: str) -> Tuple[str, bool, str]:
    if xy == "??":
        return "added", False, "untracked"
    if "R" in xy:
        return "renamed", True, "modified"
    if "D" in xy and "A" not in xy:
        return "deleted", True, "deleted"
    if "A" in xy or "C" in xy:
        return "added", False, "untracked"
    return "modified", True, "modified"


def _diff_for_path(root: Path, path: str, *, untracked: bool) -> str:
    if untracked:
        completed = _run_git(
            root,
            "diff",
            "--no-index",
            "--",
            "/dev/null",
            path,
        )
    else:
        completed = _run_git(root, "diff", "HEAD", "--", path)
    text = completed.stdout or ""
    if _looks_binary(text):
        return "Binary file {} omitted.".format(path)
    return _cap_diff(text)


def _looks_binary(diff: str) -> bool:
    return "\0" in diff or "Binary files " in diff or "GIT binary patch" in diff


def _cap_diff(diff: str) -> str:
    if len(diff) <= MAX_DIFF_CHARS:
        return diff
    budget = max(0, MAX_DIFF_CHARS - len(DIFF_TRUNCATION))
    return diff[:budget] + DIFF_TRUNCATION
