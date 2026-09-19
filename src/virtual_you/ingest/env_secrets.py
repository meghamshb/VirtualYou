"""Load sensitive values from local dotenv files without persisting them."""

import os
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Union

from virtual_you.ingest.redact import is_sensitive_key


PathLike = Union[str, Path]
_ENV_FILENAMES = (
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.test",
)
_IGNORED_VALUES = {
    "",
    "true",
    "false",
    "yes",
    "no",
    "on",
    "off",
    "null",
    "none",
    "default",
    "changeme",
    "placeholder",
    "your-api-key-here",
}
_MIN_VALUE_LENGTH = 6


def discover_env_secrets(start: Optional[PathLike] = None) -> Tuple[str, ...]:
    """Return sensitive dotenv values from the current project tree.

    Values are used only as an in-memory redaction allowlist. They are never
    written to activity records.
    """

    paths = list(_candidate_files(start))
    secrets: List[str] = []
    seen = set()
    for path in paths:
        for key, value in _parse_dotenv(path):
            if not is_sensitive_key(key) or not _usable_secret_value(value):
                continue
            if value in seen:
                continue
            seen.add(value)
            secrets.append(value)
    return tuple(sorted(secrets, key=len, reverse=True))


def _candidate_files(start: Optional[PathLike]) -> Iterable[Path]:
    configured = os.environ.get("VIRTUAL_YOU_ENV_FILE")
    if configured:
        yield Path(configured).expanduser()
    for root in _search_roots(Path(start or Path.cwd())):
        for name in _ENV_FILENAMES:
            yield root / name


def _search_roots(start: Path) -> List[Path]:
    roots: List[Path] = []
    current = start.expanduser()
    try:
        current = current.resolve()
    except OSError:
        current = start.expanduser()
    for _ in range(8):
        roots.append(current)
        if (current / ".git").exists():
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return roots


def _parse_dotenv(path: Path) -> List[Tuple[str, str]]:
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    entries: List[Tuple[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        parsed = _unquote_env_value(value.strip())
        entries.append((key.strip(), parsed))
    return entries


def _unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _usable_secret_value(value: str) -> bool:
    if len(value) < _MIN_VALUE_LENGTH:
        return False
    lowered = value.strip().lower()
    if lowered in _IGNORED_VALUES:
        return False
    if value.startswith("${") or value.startswith("$("):
        return False
    return True
