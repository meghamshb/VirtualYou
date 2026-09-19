import json
import sqlite3
from datetime import datetime, timezone

import pytest

from virtual_you.ingest.cursor import (
    discover_cursor_source,
    parse_cursor_jsonl,
    parse_cursor_source,
    read_cursor_state_db,
)
from virtual_you.ingest.errors import IngestionError, IngestionErrorCode
from virtual_you.ingest.events import (
    AssistantTextEvent,
    FileChangeEvent,
    ToolCallEvent,
    ToolResultEvent,
    UserPromptEvent,
)
from virtual_you.ingest.normalize import normalize_events


def _write_jsonl(path, records):
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def test_parse_modern_cursor_transcript(tmp_path):
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "type": "user_message",
                "sessionId": "session-1",
                "timestamp": "2026-09-19T01:02:03Z",
                "content": "Add a greeting",
            },
            {
                "type": "assistant_message",
                "sessionId": "session-1",
                "timestamp": 1_758_245_324_000,
                "content": [
                    {"type": "text", "text": "I will update it."},
                    {
                        "type": "tool_call",
                        "id": "call-1",
                        "name": "edit_file",
                        "arguments": {
                            "file_path": "src/app.py",
                            "operation": "edit",
                            "patch": "@@ greeting",
                        },
                    },
                ],
            },
            {
                "message": {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "content": "done",
                },
                "createdAt": "2026-09-19T01:02:05+00:00",
            },
        ],
    )

    events = parse_cursor_jsonl(transcript)

    prompt = next(event for event in events if isinstance(event, UserPromptEvent))
    assert prompt.text == "Add a greeting"
    assert prompt.session_id == "session-1"
    assert prompt.timestamp.tzinfo == timezone.utc
    assert any(
        isinstance(event, AssistantTextEvent)
        and event.text == "I will update it."
        for event in events
    )
    call = next(event for event in events if isinstance(event, ToolCallEvent))
    assert call.call_id == "call-1"
    assert call.input["file_path"] == "src/app.py"
    change = next(event for event in events if isinstance(event, FileChangeEvent))
    assert change.path == "src/app.py"
    assert change.operation == "modified"
    result = next(event for event in events if isinstance(event, ToolResultEvent))
    assert result.call_id == "call-1"
    assert result.content == "done"


def test_parse_apply_patch_and_strreplace_file_changes(tmp_path):
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "role": "user",
                "message": {"role": "user", "content": "Track the edits"},
            },
            {
                "role": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "patch-1",
                            "name": "ApplyPatch",
                            "input": (
                                "*** Begin Patch\n"
                                "*** Add File: src/app.py\n"
                                "+print('hi')\n"
                                "*** Update File: README.md\n"
                                "+updated\n"
                                "*** Delete File: obsolete.txt\n"
                                "*** End Patch\n"
                            ),
                        },
                        {
                            "type": "tool_use",
                            "id": "edit-1",
                            "name": "StrReplace",
                            "input": {
                                "path": "src/app.py",
                                "old_string": "print('hi')",
                                "new_string": "print('hello')",
                            },
                        },
                    ],
                },
            },
        ],
    )

    events = parse_cursor_jsonl(transcript)
    changes = [event for event in events if isinstance(event, FileChangeEvent)]

    assert [(change.operation, change.path) for change in changes] == [
        ("added", "src/app.py"),
        ("modified", "README.md"),
        ("deleted", "obsolete.txt"),
        ("modified", "src/app.py"),
    ]
    assert "print('hi')" in (changes[0].diff or "")
    assert changes[3].diff == "print('hi')\n---\nprint('hello')"


def test_read_state_database_filters_keys_and_returns_events_only(tmp_path):
    database = tmp_path / "state.vscdb"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
    connection.execute(
        "INSERT INTO ItemTable VALUES (?, ?)",
        (
            "composer.chat.session",
            json.dumps(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": "From SQLite",
                            "timestamp": "2026-09-19T02:00:00Z",
                        },
                        {
                            "type": "tool_result",
                            "toolCallId": "sqlite-call",
                            "result": {"ok": True},
                        },
                    ]
                }
            ),
        ),
    )
    connection.execute(
        "INSERT INTO ItemTable VALUES (?, ?)",
        ("unrelated.setting", "not-json-and-must-not-be-read"),
    )
    connection.commit()
    before = connection.execute("SELECT key, value FROM ItemTable").fetchall()
    connection.close()

    events = read_cursor_state_db(database)

    assert [event.kind for event in events] == ["user_prompt", "tool_result"]
    assert events[0].text == "From SQLite"
    assert not any(isinstance(event, tuple) for event in events)
    verification = sqlite3.connect(database)
    after = verification.execute("SELECT key, value FROM ItemTable").fetchall()
    verification.close()
    assert after == before


def test_discovery_prefers_jsonl_over_state_database(tmp_path):
    database = tmp_path / "state.vscdb"
    database.touch()
    transcript = tmp_path / "agent-transcripts" / "session" / "transcript.jsonl"
    transcript.parent.mkdir(parents=True)
    _write_jsonl(transcript, [{"type": "user_message", "content": "preferred"}])

    assert discover_cursor_source(tmp_path) == transcript
    events = parse_cursor_source(tmp_path)
    assert events[0].text == "preferred"


def test_invalid_json_has_stable_error_and_line_number(tmp_path):
    transcript = tmp_path / "broken.jsonl"
    transcript.write_text('{"type": "user_message", "content": "ok"}\n{\n')

    with pytest.raises(IngestionError) as error:
        parse_cursor_jsonl(transcript)

    assert error.value.code is IngestionErrorCode.MALFORMED_INPUT
    assert str(error.value) == "Cursor source contains malformed JSON."
    assert error.value.line_number == 2


def test_unknown_transcript_event_fails_loudly(tmp_path):
    transcript = tmp_path / "agent-transcript.jsonl"
    _write_jsonl(
        transcript,
        [{"type": "future_event", "payload": "unknown"}],
    )

    with pytest.raises(IngestionError) as error:
        parse_cursor_jsonl(transcript)

    assert error.value.code is IngestionErrorCode.UNSUPPORTED_EVENT
    assert error.value.line_number == 1


def test_empty_transcript_returns_nothing_to_report(tmp_path):
    transcript = tmp_path / "empty.jsonl"
    transcript.write_text("\n", encoding="utf-8")

    with pytest.raises(IngestionError) as error:
        parse_cursor_jsonl(transcript)

    assert error.value.code is IngestionErrorCode.NOTHING_TO_REPORT


def test_unsupported_source_has_stable_error(tmp_path):
    source = tmp_path / "session.txt"
    source.write_text("not a supported source", encoding="utf-8")

    with pytest.raises(IngestionError) as error:
        parse_cursor_source(source)

    assert error.value.code is IngestionErrorCode.UNSUPPORTED_EVENT
    assert str(error.value) == "Unsupported Cursor source."


def test_parses_timestamp_and_user_query_markup(tmp_path):
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "role": "user",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "<timestamp>Saturday, Sep 19, 2026, 9:40 AM "
                                "(UTC+8)</timestamp>\n"
                                "<user_query>\nAdd a greeting\n</user_query>"
                            ),
                        }
                    ]
                },
            }
        ],
    )

    events = parse_cursor_jsonl(transcript)
    prompt = next(event for event in events if isinstance(event, UserPromptEvent))
    assert prompt.text == "Add a greeting"
    assert prompt.timestamp == datetime(2026, 9, 19, 1, 40, tzinfo=timezone.utc)


def test_turn_ended_pairs_tool_calls_without_ids(tmp_path):
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "role": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Write",
                            "input": {
                                "path": "src/health.py",
                                "contents": "ok = True",
                            },
                        },
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {"path": "README.md"},
                        },
                    ]
                },
            },
            {"type": "turn_ended", "status": "success"},
        ],
    )

    events = parse_cursor_jsonl(transcript)
    calls = [event for event in events if isinstance(event, ToolCallEvent)]
    results = [event for event in events if isinstance(event, ToolResultEvent)]

    assert [call.call_id for call in calls] == [
        "cursor-tool-1-Write-0",
        "cursor-tool-1-Read-1",
    ]
    assert [result.call_id for result in results] == [
        "cursor-tool-1-Write-0",
        "cursor-tool-1-Read-1",
    ]
    assert all(not result.is_error for result in results)
    normalized = normalize_events(events, source="cursor")
    assert [call["status"] for call in normalized["tool_calls"]] == [
        "succeeded",
        "succeeded",
    ]


def test_cursor_task_window_uses_last_completed_work(tmp_path):
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "role": "user",
                "message": {
                    "content": (
                        "<timestamp>Saturday, Sep 19, 2026, 9:40 AM (UTC+8)</timestamp>"
                        "<user_query>plan the work</user_query>"
                    )
                },
            },
            {
                "role": "assistant",
                "message": {"content": "I will start with a plan."},
            },
            {
                "role": "user",
                "message": {
                    "content": (
                        "<timestamp>Saturday, Sep 19, 2026, 10:00 AM (UTC+8)</timestamp>"
                        "<user_query>add the health endpoint</user_query>"
                    )
                },
            },
            {
                "role": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "The health-check endpoint is complete."},
                        {
                            "type": "tool_use",
                            "name": "Write",
                            "input": {
                                "path": "src/health.py",
                                "contents": "def health(): return {'ok': True}",
                            },
                        },
                    ]
                },
            },
            {"type": "turn_ended", "status": "success"},
            {
                "role": "user",
                "message": {
                    "content": (
                        "<timestamp>Saturday, Sep 19, 2026, 11:00 AM (UTC+8)</timestamp>"
                        "<user_query>what branch is this?</user_query>"
                    )
                },
            },
            {
                "role": "assistant",
                "message": {"content": "You are on phase-1-test."},
            },
        ],
    )

    normalized = normalize_events(parse_cursor_jsonl(transcript), source="cursor")
    assert normalized["start_state"] == "add the health endpoint"
    assert normalized["end_state"] == "The health-check endpoint is complete."
    assert normalized["prompts"] == ["add the health endpoint"]
    assert normalized["files_changed"][0]["path"] == "src/health.py"
    assert "You are on phase-1-test." not in normalized["end_state"]
    assert normalized["reasoning_summary"] == (
        "The health-check endpoint is complete."
    )
