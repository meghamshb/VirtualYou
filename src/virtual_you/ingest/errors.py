"""Stable, secret-safe operational errors for ingestion callers."""

from enum import Enum
from typing import Optional


class IngestionErrorCode(str, Enum):
    NOTHING_TO_REPORT = "nothing_to_report"
    MALFORMED_INPUT = "malformed_input"
    UNSUPPORTED_EVENT = "unsupported_event"
    SOURCE_NOT_FOUND = "source_not_found"
    UNSAFE_OUTPUT = "unsafe_output"
    STORAGE_ERROR = "storage_error"


class IngestionError(Exception):
    def __init__(
        self,
        code: IngestionErrorCode,
        message: str,
        *,
        line_number: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.line_number = line_number

    def as_dict(self) -> dict:
        payload = {"code": self.code.value, "message": str(self)}
        if self.line_number is not None:
            payload["line_number"] = self.line_number
        return payload
