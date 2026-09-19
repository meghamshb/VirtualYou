"""Normalize raw ingestion events into an unredacted activity mapping."""

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

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

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_WINDOWED_SOURCES = frozenset({"claude", "cursor", "codex"})
_SENTENCE_BOUNDARY = re.compile(r"[.!?](?:\s+|$)")
_REASONING_PREFIX = "Reasoning occurred; omitted."
_APPROACH_LIMIT = 500


def _input_summary(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _first_session_id(events: Iterable[RawEvent], default: str) -> str:
    for event in events:
        if event.session_id:
            return event.session_id
    return default


def normalize_events(
    events: Iterable[RawEvent],
    *,
    source: str = "claude",
    default_session_id: Optional[str] = None,
    fallback_timestamp: Optional[datetime] = None,
    latest_work_only: bool = True,
) -> Dict[str, Any]:
    """Produce deterministic, unredacted data for the later policy boundary."""

    materialized = list(events)
    if not materialized:
        raise IngestionError(
            IngestionErrorCode.NOTHING_TO_REPORT,
            "No raw events were provided for normalization",
        )
    if latest_work_only and source in _WINDOWED_SOURCES:
        materialized = _task_window(materialized)

    prompts: List[str] = []
    assistant_text: List[str] = []
    end_results: List[str] = []
    reasoning_count = 0
    timestamps: List[datetime] = []
    files_changed: List[Dict[str, Optional[str]]] = []
    diffs: List[str] = []
    seen_files: Set[Tuple[str, str, Optional[str]]] = set()
    seen_diffs: Set[str] = set()
    calls: List[Dict[str, Any]] = []
    call_indexes: Dict[str, int] = {}
    pending_results: Dict[str, ToolResultEvent] = {}

    for event in materialized:
        if event.timestamp is not None:
            timestamps.append(event.timestamp)
        if isinstance(event, UserPromptEvent):
            if event.text.strip():
                prompts.append(event.text.strip())
        elif isinstance(event, AssistantTextEvent):
            if event.text.strip():
                assistant_text.append(event.text.strip())
        elif isinstance(event, ReasoningEvent):
            reasoning_count += 1
        elif isinstance(event, FileChangeEvent):
            if event.operation == "read":
                continue
            key = (event.path, event.operation, event.previous_path)
            if event.path and key not in seen_files:
                seen_files.add(key)
                files_changed.append(
                    {
                        "path": event.path,
                        "operation": event.operation,
                        "previous_path": event.previous_path,
                    }
                )
            if event.diff and event.diff not in seen_diffs:
                seen_diffs.add(event.diff)
                diffs.append(event.diff)
        elif isinstance(event, ToolCallEvent):
            call = {
                "call_id": event.call_id,
                "name": event.name,
                "input_summary": _input_summary(event.input),
                "result_summary": "",
                "status": "requested",
                "timestamp": event.timestamp,
            }
            call_indexes[event.call_id] = len(calls)
            calls.append(call)
            prior_result = pending_results.pop(event.call_id, None)
            if prior_result is not None:
                call["result_summary"] = prior_result.content
                call["status"] = "unknown" if prior_result.content.startswith("Turn ended (") else ("failed" if prior_result.is_error else "succeeded")
        elif isinstance(event, ToolResultEvent):
            call_index = call_indexes.get(event.call_id)
            if call_index is None:
                pending_results[event.call_id] = event
            else:
                calls[call_index]["result_summary"] = event.content
                calls[call_index]["status"] = (
                    "unknown" if event.content.startswith("Turn ended (") else ("failed" if event.is_error else "succeeded")
                )
        elif isinstance(event, SessionEndEvent):
            if event.result.strip():
                end_results.append(event.result.strip())

    for call_id, result in pending_results.items():
        calls.append(
            {
                "call_id": call_id,
                "name": "unknown",
                "input_summary": "",
                "result_summary": result.content,
                "status": "failed" if result.is_error else "succeeded",
                "timestamp": result.timestamp,
            }
        )

    started_at = min(timestamps) if timestamps else (fallback_timestamp or _EPOCH)
    ended_at = max(timestamps) if timestamps else started_at
    start_state = prompts[0] if prompts else ""
    if end_results:
        end_state = end_results[-1]
    elif assistant_text:
        end_state = assistant_text[-1]
    else:
        end_state = ""

    return {
        "session_id": _first_session_id(
            materialized,
            default_session_id or "{}-session".format(source),
        ),
        "source": source,
        "start_state": start_state,
        "prompts": prompts,
        "reasoning_summary": _reasoning_summary(assistant_text, reasoning_count),
        "files_changed": files_changed,
        "diffs": diffs,
        "tool_calls": calls,
        "end_state": end_state,
        "timestamp_range": {
            "started_at": started_at,
            "ended_at": ended_at,
        },
    }


def _task_window(events: List[RawEvent]) -> List[RawEvent]:
    """Keep the last user prompt that produced a file change, not setup or Q&A."""

    windows: List[List[RawEvent]] = []
    current: List[RawEvent] = []
    for event in events:
        if isinstance(event, UserPromptEvent) and current:
            windows.append(current)
            current = [event]
        else:
            current.append(event)
    if current:
        windows.append(current)

    work_windows = [
        window
        for window in windows
        if any(
            isinstance(event, FileChangeEvent) and event.operation != "read"
            for event in window
        )
    ]
    return work_windows[-1] if work_windows else events


def _reasoning_summary(assistant_text: List[str], reasoning_count: int) -> str:
    approach = _approach_from_assistant(assistant_text)
    if reasoning_count and approach:
        return "{} Approach: {}".format(_REASONING_PREFIX, approach)
    if reasoning_count:
        return _REASONING_PREFIX
    return approach


def _approach_from_assistant(assistant_text: List[str]) -> str:
    combined = " ".join(
        " ".join(part.split()) for part in assistant_text if part.strip()
    )
    if not combined:
        return ""
    sentences: List[str] = []
    start = 0
    for match in _SENTENCE_BOUNDARY.finditer(combined):
        piece = combined[start:match.end()].strip()
        if piece:
            sentences.append(piece)
        start = match.end()
        if len(sentences) >= 2:
            break
    if len(sentences) < 2:
        remainder = combined[start:].strip()
        if remainder:
            sentences.append(remainder)
    if not sentences:
        sentences = [combined]
    summary = " ".join(sentences[:2])
    if len(summary) > _APPROACH_LIMIT:
        return summary[:_APPROACH_LIMIT].rstrip()
    return summary


normalize_claude_events = normalize_events

__all__ = ["normalize_claude_events", "normalize_events"]
