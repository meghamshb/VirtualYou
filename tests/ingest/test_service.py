import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from virtual_you.ingest.errors import IngestionError, IngestionErrorCode
from virtual_you.ingest.service import IngestionService
from virtual_you.ingest.store import ActivityRecordRepository


FIXTURES = Path(__file__).parents[1] / "fixtures"


def make_service(tmp_path: Path) -> IngestionService:
    return IngestionService(
        ActivityRecordRepository(tmp_path / "activities"),
        data_directory=tmp_path,
        now=lambda: datetime(2026, 9, 19, 3, tzinfo=timezone.utc),
    )


def test_ingests_claude_and_cursor_into_same_contract(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    claude = service.ingest_file("claude", FIXTURES / "claude_session.jsonl")
    cursor = service.ingest_file("cursor", FIXTURES / "cursor_session.jsonl")

    assert claude.source == "claude"
    assert cursor.source == "cursor"
    assert claude.redacted is True
    assert cursor.redacted is True
    assert claude.schema_version == cursor.schema_version == "1.0"
    assert claude.files_changed[0].path == "src/payments/callback.py"
    assert cursor.files_changed[0].path == "src/health.py"
    assert service.latest_activity() == cursor


def test_redacts_planted_secret_before_storage_and_export(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    secret = "sk-test-51Qx9ZaBcDeFgHiJkLmNoPqR"
    transcript = "\n".join(
        [
            json.dumps(
                {
                    "type": "user",
                    "sessionId": "secret-session",
                    "timestamp": "2026-09-19T01:00:00Z",
                    "message": {
                        "role": "user",
                        "content": "Use api_key={}".format(secret),
                    },
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": "secret-session",
                    "timestamp": "2026-09-19T01:01:00Z",
                    "message": {
                        "role": "assistant",
                        "content": "Finished without exposing the credential.",
                    },
                }
            ),
        ]
    )

    record = service.ingest_transcript("claude", transcript)
    serialized = record.model_dump_json()

    assert "[REDACTED]" in serialized
    assert secret not in serialized
    assert secret not in service.export()
    assert secret not in next((tmp_path / "activities").glob("*.json")).read_text()


def test_accepts_already_transcribed_voice_text(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    record = service.ingest_transcript(
        "voice",
        "Completed callback validation; staging access is still blocked.",
    )

    assert record.source == "voice"
    assert record.prompts == [
        "Completed callback validation; staging access is still blocked."
    ]
    assert record.timestamp_range.started_at == datetime(
        2026, 9, 19, 3, tzinfo=timezone.utc
    )


def test_empty_voice_transcript_fails_loudly(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    with pytest.raises(IngestionError) as error:
        service.ingest_transcript("voice", "  ")

    assert error.value.code is IngestionErrorCode.NOTHING_TO_REPORT


def test_empty_structured_transcript_returns_nothing_to_report(
    tmp_path: Path,
) -> None:
    service = make_service(tmp_path)

    with pytest.raises(IngestionError) as error:
        service.ingest_transcript("cursor", "")

    assert error.value.code is IngestionErrorCode.NOTHING_TO_REPORT


def test_watch_resumes_without_duplicates_and_merges_session(
    tmp_path: Path,
) -> None:
    service = make_service(tmp_path)
    source = tmp_path / "live.jsonl"
    first = json.dumps(
        {
            "type": "user",
            "sessionId": "live-session",
            "timestamp": "2026-09-19T01:00:00Z",
            "message": {"role": "user", "content": "Fix callback validation."},
        }
    )
    source.write_text(first + "\n", encoding="utf-8")

    initial = service.watch(path=source, source="claude")
    assert initial is not None
    assert initial.prompts == ["Fix callback validation."]
    assert service.watch(path=source, source="claude") is None

    second = json.dumps(
        {
            "type": "assistant",
            "sessionId": "live-session",
            "timestamp": "2026-09-19T01:01:00Z",
            "message": {
                "role": "assistant",
                "content": "Callback validation is complete.",
            },
        }
    )
    with source.open("a", encoding="utf-8") as handle:
        handle.write(second + "\n")

    merged = service.watch(path=source, source="claude")
    assert merged is not None
    assert merged.prompts == ["Fix callback validation."]
    assert merged.end_state == "Callback validation is complete."
    assert merged.timestamp_range.started_at.isoformat().startswith(
        "2026-09-19T01:00:00"
    )
    assert merged.timestamp_range.ended_at.isoformat().startswith(
        "2026-09-19T01:01:00"
    )
