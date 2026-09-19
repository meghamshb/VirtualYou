"""Read Codex rollout JSONL sessions without modifying Codex state."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Mapping, Optional, Union

from virtual_you.ingest.errors import IngestionError, IngestionErrorCode
from virtual_you.ingest.events import (
    AssistantTextEvent,
    RawEvent,
    ReasoningEvent,
    SessionEndEvent,
    ToolCallEvent,
    ToolResultEvent,
    UserPromptEvent,
)
from virtual_you.ingest.patches import file_changes_from_mapping, patch_text_from_value

PathLike = Union[str, Path]

_IGNORED_RECORD_TYPES = {
    "token_usage_record",
    "turn_context",
    "world_state",
    "compacted",
}
_IGNORED_EVENT_TYPES = {
    "item_completed",
    "token_count",
    "task_started",
    "thread_settings_applied",
}
_SKIP_PROMPT_PREFIXES = (
    "<app-context>",
    "<environment_context>",
    "<recommended_plugins>",
    "the following is the codex agent history",
)


def parse_codex_file(path: PathLike) -> List[RawEvent]:
    source = Path(path)
    if not source.is_file():
        raise IngestionError(
            IngestionErrorCode.SOURCE_NOT_FOUND,
            "Codex source was not found.",
        )
    try:
        return parse_codex_jsonl(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise IngestionError(
            IngestionErrorCode.STORAGE_ERROR,
            "Codex source could not be read.",
        ) from exc


def parse_codex_jsonl(text: str) -> List[RawEvent]:
    events: List[RawEvent] = []
    had_records = False
    session_id: Optional[str] = None
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            continue
        had_records = True
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise IngestionError(
                IngestionErrorCode.MALFORMED_INPUT,
                "Codex source contains malformed JSON.",
                line_number=line_number,
            ) from exc
        if not isinstance(payload, Mapping):
            raise IngestionError(
                IngestionErrorCode.UNSUPPORTED_EVENT,
                "Unsupported Codex transcript event.",
                line_number=line_number,
            )
        parsed, session_id = _events_from_record(payload, line_number, session_id)
        events.extend(parsed)
    if not had_records:
        raise IngestionError(
            IngestionErrorCode.NOTHING_TO_REPORT,
            "Codex transcript contains no records.",
        )
    if not events:
        raise IngestionError(
            IngestionErrorCode.NOTHING_TO_REPORT,
            "Codex transcript contains no reportable events.",
        )
    return events


parse_codex_jsonl_text = parse_codex_jsonl


def _events_from_record(
    record: Mapping[str, Any],
    line_number: int,
    session_id: Optional[str],
) -> tuple:
    record_type = str(record.get("type") or "")
    body = record.get("payload", record)
    if not isinstance(body, Mapping):
        body = {}
    timestamp = _parse_timestamp(record.get("timestamp") or body.get("timestamp"))
    if record_type == "session_meta":
        session_id = _optional_text(body.get("session_id") or body.get("id")) or session_id
        return [], session_id
    session_id = _optional_text(body.get("session_id") or body.get("thread_id")) or session_id
    common = {
        "line_number": line_number,
        "timestamp": timestamp,
        "session_id": session_id,
    }

    if record_type in _IGNORED_RECORD_TYPES:
        return [], session_id
    if record_type == "event_msg":
        return _event_msg_events(body, common), session_id
    if record_type == "response_item":
        return _response_item_events(body, common), session_id
    raise IngestionError(
        IngestionErrorCode.UNSUPPORTED_EVENT,
        "Unsupported Codex transcript event.",
        line_number=line_number,
    )


def _event_msg_events(body: Mapping[str, Any], common: Mapping[str, Any]) -> List[RawEvent]:
    event_type = str(body.get("type") or "")
    if event_type in _IGNORED_EVENT_TYPES:
        return []
    if event_type == "task_complete":
        result = _content_text(body.get("last_agent_message", ""))
        return [SessionEndEvent(result=result, **common)] if result else []
    raise IngestionError(
        IngestionErrorCode.UNSUPPORTED_EVENT,
        "Unsupported Codex transcript event.",
        line_number=common["line_number"],
    )


def _response_item_events(body: Mapping[str, Any], common: Mapping[str, Any]) -> List[RawEvent]:
    item_type = str(body.get("type") or "")
    if item_type == "message":
        return _message_events(body, common)
    if item_type == "reasoning":
        return [ReasoningEvent(text="", **common)]
    if item_type in {"custom_tool_call", "function_call"}:
        return _tool_call_events(body, common)
    if item_type in {"custom_tool_call_output", "function_call_output"}:
        return [_tool_result_event(body, common)]
    raise IngestionError(
        IngestionErrorCode.UNSUPPORTED_EVENT,
        "Unsupported Codex transcript event.",
        line_number=common["line_number"],
    )


def _message_events(body: Mapping[str, Any], common: Mapping[str, Any]) -> List[RawEvent]:
    role = str(body.get("role") or "").lower()
    text = _content_text(body.get("content", ""))
    if not text:
        return []
    if role == "user":
        if _should_skip_prompt(text):
            return []
        return [UserPromptEvent(text=text, **common)]
    if role == "assistant" and body.get("channel") == "analysis":
        return [ReasoningEvent(text="", **common)]
    if role == "assistant":
        return [AssistantTextEvent(text=text, **common)]
    if role in {"developer", "system"}:
        return []
    raise IngestionError(
        IngestionErrorCode.UNSUPPORTED_EVENT,
        "Unsupported Codex transcript event.",
        line_number=common["line_number"],
    )


def _tool_call_events(body: Mapping[str, Any], common: Mapping[str, Any]) -> List[RawEvent]:
    call_id = _optional_text(body.get("call_id") or body.get("id")) or "codex-tool-{}".format(
        common["line_number"]
    )
    name = _optional_text(body.get("name")) or "unknown"
    raw_input = body.get("input", body.get("arguments", ""))
    tool_input = {"value": raw_input} if not isinstance(raw_input, dict) else raw_input
    if isinstance(raw_input, str):
        extracted = patch_text_from_value(raw_input)
        if extracted:
            tool_input = {"value": extracted, "raw": raw_input}
    events: List[RawEvent] = [ToolCallEvent(call_id=call_id, name=name, input=tool_input, **common)]
    events.extend(
        file_changes_from_mapping(
            tool_input,
            call_id=call_id,
            line_number=common["line_number"],
            timestamp=common["timestamp"],
            session_id=common["session_id"],
        )
    )
    return events


def _tool_result_event(body: Mapping[str, Any], common: Mapping[str, Any]) -> ToolResultEvent:
    call_id = _optional_text(body.get("call_id") or body.get("id")) or "codex-tool"
    return ToolResultEvent(
        call_id=call_id,
        content=_content_text(body.get("output", body.get("content", ""))),
        is_error=bool(body.get("is_error") or body.get("error")),
        **common,
    )


def _should_skip_prompt(text: str) -> bool:
    lowered = text.lstrip().lower()
    return any(lowered.startswith(prefix) for prefix in _SKIP_PROMPT_PREFIXES)


def _content_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_content_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        if value.get("type") in {"image", "input_image", "image_url", "audio", "input_audio"}:
            return ""
        for key in ("text", "content", "value"):
            if key in value:
                return _content_text(value[key])
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value).strip()


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
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "parse_codex_file",
    "parse_codex_jsonl",
    "parse_codex_jsonl_text",
]


def public_session_chunks(path: PathLike):
    """Stable, bounded public transcript windows for the background collector.

    Exclude compaction snapshots, duplicated item_completed events, all private
    reasoning and internal instructions. Accept only complete JSONL lines while
    Codex is appending. Oversized individual text fields are explicitly marked.
    """
    chunk, size, session = [], 0, None
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if not line.endswith("\n"):
                break
            record = json.loads(line)
            body = record.get("payload", {})
            if not isinstance(body, dict):
                continue
            kind = record.get("type")
            if kind == "session_meta":
                session = record
                continue
            if kind == "event_msg" and body.get("type") == "task_started":
                if chunk:
                    yield ([session] if session else []) + chunk
                    chunk, size = [], 0
                continue
            if kind == "response_item":
                item_type = body.get("type")
                if item_type == "message":
                    if body.get("role") not in {"user", "assistant"} or body.get("channel") in {
                        "analysis",
                        "summary",
                    }:
                        continue
                    text = _content_text(body.get("content", ""))
                    if _should_skip_prompt(text):
                        continue
                    body = {**body, "content": text}
                elif item_type not in {
                    "custom_tool_call",
                    "custom_tool_call_output",
                    "function_call",
                    "function_call_output",
                }:
                    continue
            elif not (kind == "event_msg" and body.get("type") == "task_complete"):
                continue
            body = dict(body)
            for key in ("content", "input", "arguments", "output", "last_agent_message"):
                if key in body:
                    text = _content_text(body[key])
                    if len(text) > 20000:
                        text = text[:20000] + "\n[Source item truncated at 20000 characters.]"
                    body[key] = text
            record = {"type": kind, "timestamp": record.get("timestamp"), "payload": body}
            encoded_size = len(json.dumps(record).encode())
            if chunk and size + encoded_size > 250000:
                yield ([session] if session else []) + chunk
                chunk, size = [], 0
            chunk.append(record)
            size += encoded_size
    if chunk:
        yield ([session] if session else []) + chunk
