"""Versioned, redacted activity contract exposed by Pathway 1."""

from datetime import datetime
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceKind(str, Enum):
    CLAUDE = "claude"
    CURSOR = "cursor"
    CODEX = "codex"
    VOICE = "voice"


class FileOperation(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    UNKNOWN = "unknown"


class TimestampRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    started_at: datetime
    ended_at: datetime

    @model_validator(mode="after")
    def validate_order(self) -> "TimestampRange":
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must be at or after started_at")
        return self


class FileChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    operation: FileOperation
    previous_path: Optional[str] = None


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    input_summary: str = ""
    result_summary: str = ""
    status: Literal["requested", "succeeded", "failed", "unknown"] = "unknown"
    timestamp: Optional[datetime] = None


class ActivityRecord(BaseModel):
    """The only activity object allowed to cross the ingestion boundary."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    schema_version: Literal["1.0"] = "1.0"
    session_id: str = Field(min_length=1)
    source: SourceKind
    start_state: str = ""
    prompts: List[str] = Field(default_factory=list)
    reasoning_summary: str = ""
    files_changed: List[FileChange] = Field(default_factory=list)
    diffs: List[str] = Field(default_factory=list)
    tool_calls: List[ToolCall] = Field(default_factory=list)
    end_state: str = ""
    timestamp_range: TimestampRange
    source_path: Optional[str] = None
    redacted: Literal[True] = True

    @field_validator("source_path", mode="before")
    @classmethod
    def normalize_source_path(cls, value: object) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator(
        "start_state",
        "reasoning_summary",
        "end_state",
        mode="before",
    )
    @classmethod
    def normalize_scalar_text(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("prompts", "diffs", mode="before")
    @classmethod
    def normalize_text_lists(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [str(item).strip() for item in value if str(item).strip()]

    def has_reportable_evidence(self) -> bool:
        return bool(
            self.prompts
            or self.reasoning_summary
            or self.files_changed
            or self.diffs
            or self.tool_calls
            or self.end_state
        )
