import json
import stat
from datetime import datetime, timezone

import pytest

from virtual_you.contracts.activity import ActivityRecord, TimestampRange
from virtual_you.ingest.checkpoint import IncrementalJsonlReader
from virtual_you.ingest.store import ActivityRecordRepository
from virtual_you.ingest.voice import VoiceTranscriptAdapter


def activity(session_id, ended_at, redacted=True):
    return ActivityRecord(
        session_id=session_id,
        source="cursor",
        end_state="done",
        timestamp_range=TimestampRange(
            started_at=datetime(2026, 9, 19, 10, tzinfo=timezone.utc),
            ended_at=ended_at,
        ),
        redacted=redacted,
    )


def test_jsonl_reader_buffers_partial_line(tmp_path):
    source = tmp_path / "events.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    source.write_bytes(b'{"event": "par')

    reader = IncrementalJsonlReader(source, checkpoint)
    assert reader.read_available() == []

    with source.open("ab") as stream:
        stream.write(b'tial"}\n')

    assert reader.read_available() == [{"event": "partial"}]
    assert reader.read_available() == []


def test_jsonl_reader_resumes_without_duplicates(tmp_path):
    source = tmp_path / "events.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    source.write_text('{"number": 1}\n', encoding="utf-8")

    assert IncrementalJsonlReader(source, checkpoint).read() == [{"number": 1}]
    assert IncrementalJsonlReader(source, checkpoint).read() == []

    with source.open("a", encoding="utf-8") as stream:
        stream.write('{"number": 2}\n')
    assert IncrementalJsonlReader(source, checkpoint).read() == [{"number": 2}]


def test_repository_latest_and_deterministic_export(tmp_path):
    repository = ActivityRecordRepository(tmp_path / "records")
    older = activity(
        "../unsafe older",
        datetime(2026, 9, 19, 11, tzinfo=timezone.utc),
    )
    newer = activity(
        "newer",
        datetime(2026, 9, 19, 12, tzinfo=timezone.utc),
    )

    repository.save(newer)
    repository.save(older)

    assert repository.latest_activity() == newer
    first_export = repository.export()
    assert repository.export() == first_export
    assert [item["session_id"] for item in json.loads(first_export)] == [
        "../unsafe older",
        "newer",
    ]
    assert all(path.parent == repository.directory for path in repository.directory.iterdir())
    stored_file = next(repository.directory.glob("activity-*.json"))
    assert stat.S_IMODE(repository.directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(stored_file.stat().st_mode) == 0o600


def test_repository_rejects_unredacted_mapping(tmp_path):
    repository = ActivityRecordRepository(tmp_path)
    unsafe = activity(
        "unsafe",
        datetime(2026, 9, 19, 12, tzinfo=timezone.utc),
    ).model_dump(mode="json")
    unsafe["redacted"] = False

    with pytest.raises(ValueError, match="not redacted"):
        repository.save(unsafe)


def test_voice_adapter_rejects_empty_and_emits_neutral_event():
    adapter = VoiceTranscriptAdapter()

    with pytest.raises(ValueError, match="nonempty"):
        adapter.adapt(" \n ")

    assert adapter.adapt("  Finished the parser.  ") == {
        "event_type": "transcript",
        "source": "voice",
        "text": "Finished the parser.",
    }
