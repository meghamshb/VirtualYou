"""Typed intermediate events emitted by activity-source parsers."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Union


@dataclass(frozen=True)
class EventMetadata:
    """Location and source metadata shared by every raw event."""

    line_number: int
    timestamp: Optional[datetime] = None
    session_id: Optional[str] = None


@dataclass(frozen=True)
class UserPromptEvent(EventMetadata):
    text: str = ""
    kind: str = "user_prompt"


@dataclass(frozen=True)
class AssistantTextEvent(EventMetadata):
    text: str = ""
    kind: str = "assistant_text"


@dataclass(frozen=True)
class ReasoningEvent(EventMetadata):
    text: str = ""
    kind: str = "reasoning"


@dataclass(frozen=True)
class ToolCallEvent(EventMetadata):
    call_id: str = ""
    name: str = ""
    input: Mapping[str, Any] = field(default_factory=dict)
    kind: str = "tool_call"


@dataclass(frozen=True)
class ToolResultEvent(EventMetadata):
    call_id: str = ""
    content: str = ""
    is_error: bool = False
    kind: str = "tool_result"


@dataclass(frozen=True)
class FileChangeEvent(EventMetadata):
    path: str = ""
    operation: str = "unknown"
    call_id: Optional[str] = None
    diff: Optional[str] = None
    previous_path: Optional[str] = None
    kind: str = "file_change"


@dataclass(frozen=True)
class SessionEndEvent(EventMetadata):
    result: str = ""
    is_error: bool = False
    kind: str = "session_end"


RawEvent = Union[
    UserPromptEvent,
    AssistantTextEvent,
    ReasoningEvent,
    ToolCallEvent,
    ToolResultEvent,
    FileChangeEvent,
    SessionEndEvent,
]


__all__ = [
    "AssistantTextEvent",
    "EventMetadata",
    "FileChangeEvent",
    "RawEvent",
    "ReasoningEvent",
    "SessionEndEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "UserPromptEvent",
]
