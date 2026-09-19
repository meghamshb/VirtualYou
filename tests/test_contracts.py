import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from virtual_you.contracts import (
    ActivityRecord,
    FileChange,
    FileOperation,
    SourceKind,
    TimestampRange,
)


def test_activity_record_is_versioned_redacted_and_reportable() -> None:
    record = ActivityRecord(
        session_id="session-1",
        source=SourceKind.CLAUDE,
        prompts=[" Add validation. "],
        files_changed=[
            FileChange(path="src/api.py", operation=FileOperation.MODIFIED)
        ],
        end_state=" Tests pass. ",
        timestamp_range=TimestampRange(
            started_at=datetime(2026, 9, 19, 1, tzinfo=timezone.utc),
            ended_at=datetime(2026, 9, 19, 2, tzinfo=timezone.utc),
        ),
    )

    assert record.schema_version == "1.0"
    assert record.redacted is True
    assert record.prompts == ["Add validation."]
    assert record.end_state == "Tests pass."
    assert record.has_reportable_evidence()


def test_activity_record_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ActivityRecord(
            session_id="session-1",
            source=SourceKind.CURSOR,
            timestamp_range={
                "started_at": "2026-09-19T01:00:00Z",
                "ended_at": "2026-09-19T02:00:00Z",
            },
            raw_transcript="must never cross the boundary",
        )


def test_timestamp_range_rejects_reverse_order() -> None:
    with pytest.raises(ValidationError):
        TimestampRange(
            started_at="2026-09-19T02:00:00Z",
            ended_at="2026-09-19T01:00:00Z",
        )


def test_shared_fixture_matches_public_contract() -> None:
    fixture = Path(__file__).parent / "fixtures" / "activity_record.json"

    record = ActivityRecord.model_validate(
        json.loads(fixture.read_text(encoding="utf-8"))
    )

    assert record.session_id == "claude-demo"
    assert record.redacted is True
    assert record.source_path == "/tmp/claude-demo.jsonl"
