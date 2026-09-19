"""Shared ApplyPatch / unified-diff decoding for source adapters."""

import re
from datetime import datetime
from typing import Any, List, Mapping, Optional

from virtual_you.ingest.events import FileChangeEvent


_PATCH_TEXT_KEYS = (
    "value",
    "input",
    "patch",
    "diff",
    "contents",
    "new_string",
    "newString",
)
_PATCH_HEADER_RE = re.compile(
    r"^\*\*\*\s+(Add File|Update File|Delete File|Rename File|Move to):\s*(.+?)\s*$"
)
_UNIFIED_DIFF_RE = re.compile(
    r"^(?:--- |\+\+\+ |diff --git )(?:a/|b/)?(.+)$",
    re.MULTILINE,
)
_PATCH_MARKER = "*** Begin Patch"


def file_changes_from_mapping(
    tool_input: Mapping[str, Any],
    *,
    call_id: str,
    line_number: int,
    timestamp: Optional[datetime] = None,
    session_id: Optional[str] = None,
) -> List[FileChangeEvent]:
    patch_text = patch_text_from_mapping(tool_input)
    if not patch_text:
        return []
    return file_changes_from_patch_text(
        patch_text,
        call_id=call_id,
        line_number=line_number,
        timestamp=timestamp,
        session_id=session_id,
    )


def patch_text_from_mapping(tool_input: Mapping[str, Any]) -> Optional[str]:
    for key in _PATCH_TEXT_KEYS:
        extracted = patch_text_from_value(tool_input.get(key))
        if extracted:
            return extracted
    return None


def patch_text_from_value(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = _unescape_tool_text(value)
    if _PATCH_MARKER in text:
        return text[text.find(_PATCH_MARKER) :]
    if "*** Add File:" in text or "*** Update File:" in text:
        return text
    if text.lstrip().startswith("diff "):
        return text
    return None


def file_changes_from_patch_text(
    patch_text: str,
    *,
    call_id: str,
    line_number: int,
    timestamp: Optional[datetime] = None,
    session_id: Optional[str] = None,
) -> List[FileChangeEvent]:
    parsed: List[FileChangeEvent] = []
    current: Optional[dict] = None
    hunk_lines: List[str] = []
    common = {
        "line_number": line_number,
        "timestamp": timestamp,
        "session_id": session_id,
    }

    def flush() -> None:
        if current is None or not current.get("path"):
            return
        parsed.append(
            FileChangeEvent(
                path=str(current["path"]),
                operation=str(current.get("operation") or "modified"),
                call_id=call_id,
                diff="\n".join(hunk_lines).strip() or patch_text,
                previous_path=current.get("previous_path"),
                **common,
            )
        )

    for raw_line in patch_text.splitlines():
        match = _PATCH_HEADER_RE.match(raw_line)
        if match is None:
            if current is not None and not raw_line.startswith("*** End Patch"):
                hunk_lines.append(raw_line)
            continue
        kind, remainder = match.groups()
        if kind == "Move to" and current is not None:
            current["previous_path"] = current.get("path")
            current["path"] = remainder
            current["operation"] = "renamed"
            hunk_lines.append(raw_line)
            continue
        flush()
        hunk_lines = [raw_line]
        operation, path, previous_path = _patch_header_change(kind, remainder)
        current = {
            "path": path,
            "operation": operation,
            "previous_path": previous_path,
        }
    flush()
    if parsed:
        return parsed

    unified_path = _unified_diff_path(patch_text)
    if unified_path is None:
        return []
    return [
        FileChangeEvent(
            path=unified_path,
            operation="modified",
            call_id=call_id,
            diff=patch_text,
            **common,
        )
    ]


def _patch_header_change(kind: str, remainder: str) -> tuple:
    if kind == "Add File":
        return "added", remainder, None
    if kind == "Delete File":
        return "deleted", remainder, None
    if kind == "Rename File":
        source, separator, destination = remainder.partition(" -> ")
        if separator:
            return "renamed", destination.strip(), source.strip()
        return "renamed", remainder, None
    if kind == "Move to":
        return "renamed", remainder, None
    if kind == "Update File":
        return "modified", remainder, None
    raise AssertionError("unreachable patch header kind")


def _unified_diff_path(patch_text: str) -> Optional[str]:
    for match in _UNIFIED_DIFF_RE.finditer(patch_text):
        candidate = match.group(1).strip()
        if candidate and candidate != "/dev/null":
            return candidate.split("\t", 1)[0]
    return None


def _unescape_tool_text(text: str) -> str:
    if "\\n" not in text and '\\"' not in text:
        return text
    return (
        text.replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


__all__ = [
    "file_changes_from_mapping",
    "file_changes_from_patch_text",
    "patch_text_from_mapping",
    "patch_text_from_value",
]
