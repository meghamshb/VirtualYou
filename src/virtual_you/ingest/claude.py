"""Line-oriented parser for Claude Code JSONL session exports."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional, TextIO, Union

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

JsonlSource = Union[str, Iterable[str], TextIO]

_IGNORED_RECORD_TYPES = {
    "ai-title",
    "atis-latch",
    "attachment",
    "custom-title",
    "file-history-snapshot",
    "last-prompt",
    "mode",
    "progress",
    "queue-operation",
    "system",
}
_PATH_KEYS = ("file_path", "path", "notebook_path")
_TOOL_OPERATIONS = {
    "edit": "modified",
    "multiedit": "modified",
    "notebookedit": "modified",
    "read": "read",
    "write": "added",
}


def _error(
    code: IngestionErrorCode, message: str, line_number: Optional[int] = None
) -> IngestionError:
    return IngestionError(code, message, line_number=line_number)


def _parse_timestamp(value: object, line_number: int) -> Optional[datetime]:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric = float(value)
            if abs(numeric) >= 100_000_000_000:
                numeric /= 1000
            return datetime.fromtimestamp(numeric, tz=timezone.utc)
        if isinstance(value, str):
            candidate = value.strip()
            if candidate.replace(".", "", 1).isdigit():
                return _parse_timestamp(float(candidate), line_number)
            if candidate.endswith(("Z", "z")):
                candidate = candidate[:-1] + "+00:00"
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        pass
    raise _error(
        IngestionErrorCode.MALFORMED_INPUT,
        "Invalid timestamp in Claude event",
        line_number,
    )


def _text_content(value: object, line_number: int) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        raise _error(
            IngestionErrorCode.MALFORMED_INPUT,
            "Invalid tool result content",
            line_number,
        )
    parts: List[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise _error(
                IngestionErrorCode.MALFORMED_INPUT,
                "Invalid structured tool result",
                line_number,
            )
        if item.get("type") == "text":
            text = item.get("text")
            if not isinstance(text, str):
                raise _error(
                    IngestionErrorCode.MALFORMED_INPUT,
                    "Invalid Claude text block",
                    line_number,
                )
            parts.append(text)
        else:
            parts.append(
                json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            )
    return "\n".join(part for part in parts if part)


def _file_event(
    tool_name: str,
    tool_input: Mapping[str, Any],
    call_id: str,
    line_number: int,
    timestamp: Optional[datetime],
    session_id: Optional[str],
) -> Optional[FileChangeEvent]:
    normalized_name = tool_name.rsplit(":", 1)[-1].rsplit(".", 1)[-1].lower()
    operation = _TOOL_OPERATIONS.get(normalized_name)
    if operation is None:
        return None
    path = next(
        (
            tool_input[key]
            for key in _PATH_KEYS
            if isinstance(tool_input.get(key), str) and tool_input[key].strip()
        ),
        None,
    )
    if path is None:
        return None
    diff = None
    if normalized_name in {"edit", "multiedit", "notebookedit"}:
        old = tool_input.get("old_string")
        new = tool_input.get("new_string")
        if isinstance(old, str) or isinstance(new, str):
            diff = "{}\n---\n{}".format(old or "", new or "")
    elif normalized_name == "write":
        contents = tool_input.get("contents", tool_input.get("content"))
        if isinstance(contents, str) and contents:
            diff = contents
    return FileChangeEvent(
        line_number=line_number,
        timestamp=timestamp,
        session_id=session_id,
        path=path,
        operation=operation,
        call_id=call_id,
        diff=diff,
    )


def _parse_blocks(
    record_type: str,
    content: object,
    *,
    line_number: int,
    timestamp: Optional[datetime],
    session_id: Optional[str],
) -> List[RawEvent]:
    if isinstance(content, str):
        event_type = UserPromptEvent if record_type == "user" else AssistantTextEvent
        return [
            event_type(
                line_number=line_number,
                timestamp=timestamp,
                session_id=session_id,
                text=content,
            )
        ] if content else []
    if not isinstance(content, list):
        raise _error(
            IngestionErrorCode.MALFORMED_INPUT,
            "Claude message content must be text or a list",
            line_number,
        )

    events: List[RawEvent] = []
    for block in content:
        if not isinstance(block, Mapping) or not isinstance(block.get("type"), str):
            raise _error(
                IngestionErrorCode.MALFORMED_INPUT,
                "Invalid Claude content block",
                line_number,
            )
        block_type = block["type"]
        if block_type == "text":
            text = block.get("text")
            if not isinstance(text, str):
                raise _error(
                    IngestionErrorCode.MALFORMED_INPUT,
                    "Invalid Claude text block",
                    line_number,
                )
            event_type = (
                UserPromptEvent if record_type == "user" else AssistantTextEvent
            )
            if text:
                events.append(
                    event_type(
                        line_number=line_number,
                        timestamp=timestamp,
                        session_id=session_id,
                        text=text,
                    )
                )
        elif block_type == "thinking" and record_type == "assistant":
            thinking = block.get("thinking", block.get("text"))
            if not isinstance(thinking, str):
                raise _error(
                    IngestionErrorCode.MALFORMED_INPUT,
                    "Invalid Claude thinking block",
                    line_number,
                )
            events.append(
                ReasoningEvent(
                    line_number=line_number,
                    timestamp=timestamp,
                    session_id=session_id,
                    text=thinking,
                )
            )
        elif block_type == "tool_use" and record_type == "assistant":
            call_id = block.get("id")
            name = block.get("name")
            tool_input = block.get("input", {})
            if (
                not isinstance(call_id, str)
                or not call_id
                or not isinstance(name, str)
                or not name
                or not isinstance(tool_input, Mapping)
            ):
                raise _error(
                    IngestionErrorCode.MALFORMED_INPUT,
                    "Invalid Claude tool call",
                    line_number,
                )
            events.append(
                ToolCallEvent(
                    line_number=line_number,
                    timestamp=timestamp,
                    session_id=session_id,
                    call_id=call_id,
                    name=name,
                    input=dict(tool_input),
                )
            )
            file_event = _file_event(
                name, tool_input, call_id, line_number, timestamp, session_id
            )
            if file_event is not None:
                events.append(file_event)
        elif block_type == "tool_result" and record_type == "user":
            call_id = block.get("tool_use_id")
            if not isinstance(call_id, str) or not call_id:
                raise _error(
                    IngestionErrorCode.MALFORMED_INPUT,
                    "Invalid Claude tool result",
                    line_number,
                )
            events.append(
                ToolResultEvent(
                    line_number=line_number,
                    timestamp=timestamp,
                    session_id=session_id,
                    call_id=call_id,
                    content=_text_content(block.get("content"), line_number),
                    is_error=bool(block.get("is_error", False)),
                )
            )
        else:
            raise _error(
                IngestionErrorCode.UNSUPPORTED_EVENT,
                "Unsupported Claude content block: {}".format(block_type),
                line_number,
            )
    return events


def _parse_record(record: Mapping[str, Any], line_number: int) -> List[RawEvent]:
    record_type = record.get("type")
    if not isinstance(record_type, str):
        raise _error(
            IngestionErrorCode.MALFORMED_INPUT,
            "Claude event is missing a type",
            line_number,
        )
    message = record.get("message")
    message_mapping = message if isinstance(message, Mapping) else {}
    timestamp = _parse_timestamp(
        record.get("timestamp", message_mapping.get("timestamp")), line_number
    )
    raw_session_id = record.get(
        "sessionId", record.get("session_id", record.get("conversation_id"))
    )
    session_id = str(raw_session_id) if raw_session_id is not None else None

    if record_type in {"user", "assistant"}:
        if not isinstance(message, Mapping):
            raise _error(
                IngestionErrorCode.MALFORMED_INPUT,
                "Claude message event is missing a message",
                line_number,
            )
        return _parse_blocks(
            record_type,
            message.get("content"),
            line_number=line_number,
            timestamp=timestamp,
            session_id=session_id,
        )
    if record_type == "result":
        result = record.get("result", "")
        if not isinstance(result, str):
            raise _error(
                IngestionErrorCode.MALFORMED_INPUT,
                "Invalid Claude session result",
                line_number,
            )
        return [
            SessionEndEvent(
                line_number=line_number,
                timestamp=timestamp,
                session_id=session_id,
                result=result,
                is_error=bool(record.get("is_error", False)),
            )
        ]
    if record_type in _IGNORED_RECORD_TYPES:
        return []
    raise _error(
        IngestionErrorCode.UNSUPPORTED_EVENT,
        "Unsupported Claude event type: {}".format(record_type),
        line_number,
    )


def parse_claude_jsonl(source: JsonlSource) -> List[RawEvent]:
    """Parse JSONL text or an iterable of lines without loading JSON as an array."""

    lines = source.splitlines() if isinstance(source, str) else source
    events: List[RawEvent] = []
    saw_record = False
    for line_number, raw_line in enumerate(lines, start=1):
        if not isinstance(raw_line, str):
            raise _error(
                IngestionErrorCode.MALFORMED_INPUT,
                "Claude JSONL lines must be text",
                line_number,
            )
        if not raw_line.strip():
            continue
        saw_record = True
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError:
            raise _error(
                IngestionErrorCode.MALFORMED_INPUT,
                "Invalid Claude JSONL",
                line_number,
            )
        if not isinstance(record, Mapping):
            raise _error(
                IngestionErrorCode.MALFORMED_INPUT,
                "Claude JSONL record must be an object",
                line_number,
            )
        events.extend(_parse_record(record, line_number))
    if not saw_record or not events:
        raise _error(
            IngestionErrorCode.NOTHING_TO_REPORT,
            "Claude session contains no reportable events",
        )
    return events


def parse_claude_file(path: Union[str, Path]) -> List[RawEvent]:
    """Parse a UTF-8 Claude JSONL file."""

    source_path = Path(path)
    try:
        with source_path.open("r", encoding="utf-8") as handle:
            return parse_claude_jsonl(handle)
    except FileNotFoundError:
        raise _error(
            IngestionErrorCode.SOURCE_NOT_FOUND,
            "Claude session file was not found",
        )


parse_jsonl = parse_claude_jsonl

__all__ = ["parse_claude_file", "parse_claude_jsonl", "parse_jsonl"]
