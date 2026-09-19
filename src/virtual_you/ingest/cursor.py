"""Read Cursor agent transcripts without modifying Cursor's local state."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from virtual_you.ingest.errors import IngestionError, IngestionErrorCode
from virtual_you.ingest.events import (
    AssistantTextEvent,
    FileChangeEvent,
    RawEvent,
    ReasoningEvent,
    SessionEndEvent,
    ToolCallEvent,
    ToolResultEvent,
    UserPromptEvent,
)


PathLike = Union[str, Path]

_TRANSCRIPT_PATTERNS = (
    "agent-transcripts/**/*.jsonl",
    "**/agent-transcripts/**/*.jsonl",
    "**/*transcript*.jsonl",
    "**/*.jsonl",
)
_DB_KEY_TERMS = ("composer", "chat", "agent", "conversation")
_IGNORED_TRANSCRIPT_TYPES = {
    "metadata",
    "progress",
    "system",
    "turn_ended",
}
_CONTAINER_KEYS = (
    "messages",
    "events",
    "items",
    "bubbles",
    "conversation",
    "conversations",
    "transcript",
    "tabs",
    "composers",
    "allComposers",
    "data",
)
_PATH_KEYS = (
    "path",
    "file_path",
    "filePath",
    "target_file",
    "targetFile",
    "relative_path",
    "relativePath",
    "uri",
)


def discover_cursor_source(root: PathLike) -> Path:
    """Choose a transcript JSONL under *root*, falling back to state.vscdb."""

    source = Path(root).expanduser()
    if source.is_file():
        if source.suffix.lower() == ".jsonl" or source.name == "state.vscdb":
            return source
        raise _unsupported_source()
    if not source.exists():
        raise IngestionError(
            IngestionErrorCode.SOURCE_NOT_FOUND,
            "Cursor source was not found.",
        )
    if not source.is_dir():
        raise _unsupported_source()

    seen = set()
    for pattern in _TRANSCRIPT_PATTERNS:
        candidates = sorted(
            candidate
            for candidate in source.glob(pattern)
            if candidate.is_file() and candidate not in seen
        )
        if candidates:
            return candidates[-1]
        seen.update(candidates)

    databases = sorted(
        candidate
        for candidate in source.rglob("state.vscdb")
        if candidate.is_file()
    )
    if databases:
        return databases[-1]
    raise IngestionError(
        IngestionErrorCode.SOURCE_NOT_FOUND,
        "Cursor source was not found.",
    )


def parse_cursor_source(source: PathLike) -> List[RawEvent]:
    """Parse a Cursor transcript or its read-only state database fallback."""

    selected = discover_cursor_source(source)
    if selected.suffix.lower() == ".jsonl":
        return parse_cursor_jsonl(selected)
    if selected.name == "state.vscdb":
        return read_cursor_state_db(selected)
    raise _unsupported_source()


def parse_cursor_jsonl(path: PathLike) -> List[RawEvent]:
    """Parse modern Cursor transcript records line by line."""

    transcript = Path(path)
    if not transcript.is_file():
        raise IngestionError(
            IngestionErrorCode.SOURCE_NOT_FOUND,
            "Cursor source was not found.",
        )

    events: List[RawEvent] = []
    had_records = False
    try:
        with transcript.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                if not raw_line.strip():
                    continue
                had_records = True
                try:
                    payload = json.loads(raw_line)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise _malformed(line_number) from exc
                parsed = _events_from_payload(payload, line_number)
                _assert_supported_transcript_payload(payload, parsed, line_number)
                events.extend(parsed)
    except OSError as exc:
        raise IngestionError(
            IngestionErrorCode.STORAGE_ERROR,
            "Cursor source could not be read.",
        ) from exc

    if not had_records:
        raise IngestionError(
            IngestionErrorCode.NOTHING_TO_REPORT,
            "Cursor transcript contains no records.",
        )
    if not events:
        raise IngestionError(
            IngestionErrorCode.NOTHING_TO_REPORT,
            "Cursor transcript contains no reportable events.",
        )
    return events


def parse_cursor_jsonl_text(text: str) -> List[RawEvent]:
    """Parse Cursor JSONL already loaded in memory."""

    events: List[RawEvent] = []
    had_records = False
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            continue
        had_records = True
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise _malformed(line_number) from exc
        parsed = _events_from_payload(payload, line_number)
        _assert_supported_transcript_payload(payload, parsed, line_number)
        events.extend(parsed)
    if not had_records:
        raise IngestionError(
            IngestionErrorCode.NOTHING_TO_REPORT,
            "Cursor transcript contains no records.",
        )
    if not events:
        raise IngestionError(
            IngestionErrorCode.NOTHING_TO_REPORT,
            "Cursor transcript contains no reportable events.",
        )
    return events


def read_cursor_state_db(path: PathLike) -> List[RawEvent]:
    """Read likely chat payloads from ItemTable using SQLite read-only mode."""

    database = Path(path)
    if not database.is_file():
        raise IngestionError(
            IngestionErrorCode.SOURCE_NOT_FOUND,
            "Cursor source was not found.",
        )

    connection: Optional[sqlite3.Connection] = None
    events: List[RawEvent] = []
    try:
        uri = "{}?mode=ro".format(database.resolve().as_uri())
        connection = sqlite3.connect(uri, uri=True)
        query = (
            "SELECT key, value FROM ItemTable WHERE "
            + " OR ".join("lower(key) LIKE ?" for _ in _DB_KEY_TERMS)
        )
        parameters = tuple("%{}%".format(term) for term in _DB_KEY_TERMS)
        rows = connection.execute(query, parameters)
        for row_number, (_, value) in enumerate(rows, start=1):
            if not isinstance(value, (str, bytes, bytearray)):
                continue
            try:
                payload = json.loads(value)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise _malformed(row_number) from exc
            events.extend(_events_from_payload(payload, row_number))
    except IngestionError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise IngestionError(
            IngestionErrorCode.STORAGE_ERROR,
            "Cursor state database could not be read.",
        ) from exc
    finally:
        if connection is not None:
            connection.close()
    return events


def _events_from_payload(payload: Any, line_number: int) -> List[RawEvent]:
    records = list(_iter_records(payload))
    events: List[RawEvent] = []
    for record in records:
        events.extend(_map_record(record, line_number))
    return events


def _assert_supported_transcript_payload(
    payload: Any,
    events: Sequence[RawEvent],
    line_number: int,
) -> None:
    if events or not isinstance(payload, Mapping):
        return
    record_type = _record_type(payload)
    role = str(payload.get("role", "")).lower()
    if record_type in _IGNORED_TRANSCRIPT_TYPES:
        return
    if role in {"user", "assistant", "tool"}:
        return
    raise IngestionError(
        IngestionErrorCode.UNSUPPORTED_EVENT,
        "Unsupported Cursor transcript event.",
        line_number=line_number,
    )


def _iter_records(payload: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_records(item)
        return
    if not isinstance(payload, dict):
        return
    if _looks_like_event(payload):
        yield payload
        return
    for key in _CONTAINER_KEYS:
        child = payload.get(key)
        if isinstance(child, (dict, list)):
            yield from _iter_records(child)


def _looks_like_event(record: Mapping[str, Any]) -> bool:
    if str(record.get("role", "")).lower() in {"user", "assistant", "tool"}:
        return True
    record_type = _record_type(record)
    if record_type in {
        "user",
        "user_message",
        "assistant",
        "assistant_message",
        "tool_call",
        "tool_use",
        "tool_result",
        "file_change",
        "session_end",
        "result",
        "reasoning",
        "thinking",
    }:
        return True
    if str(record.get("role", "")).lower() in {"user", "assistant", "tool"}:
        return True
    message = record.get("message")
    return isinstance(message, dict) and str(message.get("role", "")).lower() in {
        "user",
        "assistant",
        "tool",
    }


def _map_record(record: Mapping[str, Any], line_number: int) -> List[RawEvent]:
    message = record.get("message")
    body = message if isinstance(message, dict) else record
    record_type = _record_type(record)
    role = str(body.get("role", record.get("role", ""))).lower()
    if not role:
        if record_type.startswith("user"):
            role = "user"
        elif record_type.startswith("assistant"):
            role = "assistant"

    timestamp = _parse_timestamp(
        _first_value(body, record, keys=("timestamp", "createdAt", "created_at", "time"))
    )
    session_id = _optional_text(
        _first_value(
            body,
            record,
            keys=("session_id", "sessionId", "conversationId", "composerId", "chatId"),
        )
    )
    common = {
        "line_number": line_number,
        "timestamp": timestamp,
        "session_id": session_id,
    }

    if record_type in {"tool_call", "tool_use"}:
        return _tool_call_events(body, common)
    if record_type == "tool_result" or role == "tool":
        return [_tool_result_event(body, common)]
    if record_type == "file_change":
        change = _file_change_event(body, common)
        return [change] if change is not None else []
    if record_type in {"session_end", "result"}:
        return [
            SessionEndEvent(
                result=_content_text(body.get("result", body.get("content", ""))),
                is_error=bool(body.get("is_error", body.get("isError", False))),
                **common,
            )
        ]
    if record_type in {"reasoning", "thinking"}:
        text = _content_text(body.get("content", body.get("text", "")))
        return [ReasoningEvent(text=text, **common)] if text else []

    content = body.get("content", body.get("text", ""))
    if role == "user":
        text = _content_text(content)
        return [UserPromptEvent(text=text, **common)] if text else []
    if role == "assistant":
        return _assistant_content_events(content, common)
    return []


def _assistant_content_events(
    content: Any, common: Mapping[str, Any]
) -> List[RawEvent]:
    if isinstance(content, str):
        return [AssistantTextEvent(text=content, **common)] if content.strip() else []
    if isinstance(content, dict):
        content = [content]
    if not isinstance(content, list):
        return []

    events: List[RawEvent] = []
    for block in content:
        if isinstance(block, str):
            if block.strip():
                events.append(AssistantTextEvent(text=block, **common))
            continue
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type", "")).lower()
        if block_type in {"text", "output_text", "assistant_text"}:
            text = _content_text(block.get("text", block.get("content", "")))
            if text:
                events.append(AssistantTextEvent(text=text, **common))
        elif block_type in {"thinking", "reasoning"}:
            text = _content_text(block.get("thinking", block.get("text", "")))
            if text:
                events.append(ReasoningEvent(text=text, **common))
        elif block_type in {"tool_call", "tool_use"}:
            events.extend(_tool_call_events(block, common))
        elif block_type == "tool_result":
            events.append(_tool_result_event(block, common))
    return events


def _tool_call_events(
    record: Mapping[str, Any], common: Mapping[str, Any]
) -> List[RawEvent]:
    call_id = _optional_text(
        _first_value(record, keys=("call_id", "tool_call_id", "toolCallId", "id"))
    ) or "cursor-tool-{}".format(common["line_number"])
    function = record.get("function")
    function_data = function if isinstance(function, dict) else {}
    name = _optional_text(
        _first_value(
            record,
            function_data,
            keys=("name", "tool_name", "toolName"),
        )
    ) or "unknown"
    raw_input = _first_value(
        record,
        function_data,
        keys=("input", "arguments", "args", "parameters"),
    )
    tool_input = _mapping_input(raw_input)
    events: List[RawEvent] = [
        ToolCallEvent(call_id=call_id, name=name, input=tool_input, **common)
    ]
    file_change = _file_change_from_tool(name, call_id, tool_input, common)
    if file_change is not None:
        events.append(file_change)
    return events


def _tool_result_event(
    record: Mapping[str, Any], common: Mapping[str, Any]
) -> ToolResultEvent:
    call_id = _optional_text(
        _first_value(record, keys=("call_id", "tool_call_id", "toolCallId", "id"))
    ) or "cursor-tool"
    content = _content_text(
        _first_value(record, keys=("content", "result", "output", "text"))
    )
    is_error = bool(
        _first_value(record, keys=("is_error", "isError", "error", "failed"))
    )
    return ToolResultEvent(
        call_id=call_id,
        content=content,
        is_error=is_error,
        **common,
    )


def _file_change_from_tool(
    name: str,
    call_id: str,
    tool_input: Mapping[str, Any],
    common: Mapping[str, Any],
) -> Optional[FileChangeEvent]:
    lowered = name.lower()
    operation = _operation(
        _first_value(tool_input, keys=("operation", "op", "action")), lowered
    )
    if operation == "unknown" and not any(
        token in lowered
        for token in ("edit", "write", "create", "delete", "remove", "rename", "move", "patch")
    ):
        return None
    path = _path_from_mapping(tool_input)
    if not path:
        return None
    previous_path = _optional_text(
        _first_value(
            tool_input,
            keys=("previous_path", "previousPath", "old_path", "oldPath", "from"),
        )
    )
    diff = _optional_text(
        _first_value(tool_input, keys=("diff", "patch", "changes"))
    )
    return FileChangeEvent(
        path=path,
        operation=operation,
        call_id=call_id,
        diff=diff,
        previous_path=previous_path,
        **common,
    )


def _file_change_event(
    record: Mapping[str, Any], common: Mapping[str, Any]
) -> Optional[FileChangeEvent]:
    path = _path_from_mapping(record)
    if not path:
        return None
    return FileChangeEvent(
        path=path,
        operation=_operation(
            _first_value(record, keys=("operation", "op", "action")), ""
        ),
        call_id=_optional_text(
            _first_value(record, keys=("call_id", "tool_call_id", "toolCallId"))
        ),
        diff=_optional_text(_first_value(record, keys=("diff", "patch"))),
        previous_path=_optional_text(
            _first_value(
                record,
                keys=("previous_path", "previousPath", "old_path", "oldPath"),
            )
        ),
        **common,
    )


def _mapping_input(value: Any) -> Mapping[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {"value": value}
        if isinstance(decoded, dict):
            return decoded
    return {}


def _path_from_mapping(value: Mapping[str, Any]) -> Optional[str]:
    direct = _optional_text(_first_value(value, keys=_PATH_KEYS))
    if direct:
        return direct[7:] if direct.startswith("file://") else direct
    for key in ("file", "target", "location"):
        nested = value.get(key)
        if isinstance(nested, dict):
            found = _path_from_mapping(nested)
            if found:
                return found
    return None


def _operation(value: Any, tool_name: str) -> str:
    operation = str(value or "").lower()
    combined = "{} {}".format(operation, tool_name)
    if "rename" in combined or "move" in combined:
        return "renamed"
    if "delete" in combined or "remove" in combined:
        return "deleted"
    if "create" in combined or "write" in combined or "add" in combined:
        return "added"
    if "edit" in combined or "patch" in combined or "modify" in combined:
        return "modified"
    return "unknown"


def _record_type(record: Mapping[str, Any]) -> str:
    return str(
        _first_value(record, keys=("type", "event_type", "eventType", "kind")) or ""
    ).lower()


def _content_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [_content_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("text", "content", "value"):
            if key in value:
                return _content_text(value[key])
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value)


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if abs(seconds) > 100_000_000_000:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return _parse_timestamp(int(text))
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _first_value(
    *mappings: Mapping[str, Any], keys: Sequence[str]
) -> Any:
    for mapping in mappings:
        for key in keys:
            if key in mapping and mapping[key] is not None:
                return mapping[key]
    return None


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _malformed(line_number: int) -> IngestionError:
    return IngestionError(
        IngestionErrorCode.MALFORMED_INPUT,
        "Cursor source contains malformed JSON.",
        line_number=line_number,
    )


def _unsupported_source() -> IngestionError:
    return IngestionError(
        IngestionErrorCode.UNSUPPORTED_EVENT,
        "Unsupported Cursor source.",
    )


# Friendly aliases for callers that name the adapter rather than its format.
parse_cursor_transcript = parse_cursor_jsonl
read_cursor_sqlite = read_cursor_state_db
load_cursor_events = parse_cursor_source


__all__ = [
    "discover_cursor_source",
    "load_cursor_events",
    "parse_cursor_jsonl",
    "parse_cursor_jsonl_text",
    "parse_cursor_source",
    "parse_cursor_transcript",
    "read_cursor_sqlite",
    "read_cursor_state_db",
]
