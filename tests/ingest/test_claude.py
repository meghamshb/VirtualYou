import json
from datetime import datetime, timezone
from io import StringIO

import pytest

from virtual_you.ingest.claude import parse_claude_jsonl
from virtual_you.ingest.errors import IngestionError, IngestionErrorCode
from virtual_you.ingest.events import (
    AssistantTextEvent,
    FileChangeEvent,
    ReasoningEvent,
    SessionEndEvent,
    ToolCallEvent,
    ToolResultEvent,
    UserPromptEvent,
)
from virtual_you.ingest.normalize import normalize_events


def _line(value):
    return json.dumps(value)


def test_parses_messages_blocks_tools_files_and_session_end():
    source = "\n".join(
        [
            _line(
                {
                    "type": "user",
                    "sessionId": "session-1",
                    "timestamp": "2026-09-19T01:00:00Z",
                    "message": {"content": "Implement the parser"},
                }
            ),
            _line(
                {
                    "type": "assistant",
                    "sessionId": "session-1",
                    "timestamp": 1_789_779_601_000,
                    "message": {
                        "content": [
                            {"type": "thinking", "thinking": "A private plan."},
                            {"type": "text", "text": "I will update the parser."},
                            {
                                "type": "tool_use",
                                "id": "tool-1",
                                "name": "Edit",
                                "input": {
                                    "file_path": "src/parser.py",
                                    "old_string": "old",
                                    "new_string": "new",
                                },
                            },
                        ]
                    },
                }
            ),
            _line(
                {
                    "type": "user",
                    "sessionId": "session-1",
                    "timestamp": "2026-09-19T01:00:02+00:00",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "tool-1",
                                "content": [{"type": "text", "text": "Updated"}],
                            }
                        ]
                    },
                }
            ),
            _line(
                {
                    "type": "result",
                    "sessionId": "session-1",
                    "timestamp": "2026-09-19T01:00:03Z",
                    "result": "Parser implemented",
                }
            ),
        ]
    )

    events = parse_claude_jsonl(StringIO(source))

    assert [type(event) for event in events] == [
        UserPromptEvent,
        ReasoningEvent,
        AssistantTextEvent,
        ToolCallEvent,
        FileChangeEvent,
        ToolResultEvent,
        SessionEndEvent,
    ]
    assert events[4].path == "src/parser.py"
    assert events[4].operation == "modified"
    assert events[1].timestamp == datetime(
        2026, 9, 19, 1, 0, 1, tzinfo=timezone.utc
    )


def test_extracts_common_tool_paths_and_operations():
    blocks = []
    expected = [
        ("one.py", "added"),
        ("two.py", "modified"),
        ("three.ipynb", "modified"),
        ("four.py", "read"),
    ]
    for index, (name, path_key, path) in enumerate(
        [
            ("Write", "file_path", "one.py"),
            ("Edit", "path", "two.py"),
            ("NotebookEdit", "notebook_path", "three.ipynb"),
            ("Read", "file_path", "four.py"),
        ]
    ):
        payload = {path_key: path}
        if name == "Write":
            payload["contents"] = "print('created')\n"
        blocks.append(
            {
                "type": "tool_use",
                "id": "call-{}".format(index),
                "name": name,
                "input": payload,
            }
        )

    events = parse_claude_jsonl(
        _line({"type": "assistant", "message": {"content": blocks}})
    )

    changes = [
        event for event in events if isinstance(event, FileChangeEvent)
    ]
    assert [
        (event.path, event.operation)
        for event in changes
    ] == expected
    assert changes[0].diff == "print('created')\n"


def test_normalizes_correlates_deduplicates_and_omits_chain_of_thought():
    source = "\n".join(
        [
            _line(
                {
                    "type": "user",
                    "sessionId": "s",
                    "timestamp": "2026-09-19T10:00:00Z",
                    "message": {"content": "Fix it"},
                }
            ),
            _line(
                {
                    "type": "assistant",
                    "sessionId": "s",
                    "timestamp": "2026-09-19T10:00:01Z",
                    "message": {
                        "content": [
                            {
                                "type": "thinking",
                                "thinking": "Secret internal reasoning details",
                            },
                            {
                                "type": "tool_use",
                                "id": "x",
                                "name": "Edit",
                                "input": {"file_path": "a.py"},
                            },
                            {
                                "type": "tool_use",
                                "id": "y",
                                "name": "Edit",
                                "input": {"file_path": "a.py"},
                            },
                        ]
                    },
                }
            ),
            _line(
                {
                    "type": "user",
                    "sessionId": "s",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "x",
                                "content": "done",
                            },
                            {
                                "type": "tool_result",
                                "tool_use_id": "y",
                                "content": "failed",
                                "is_error": True,
                            },
                        ]
                    },
                }
            ),
            _line(
                {
                    "type": "result",
                    "sessionId": "s",
                    "timestamp": "2026-09-19T10:00:03Z",
                    "result": "Fixed",
                }
            ),
        ]
    )

    normalized = normalize_events(parse_claude_jsonl(source))

    assert normalized["session_id"] == "s"
    assert normalized["start_state"] == "Fix it"
    assert normalized["end_state"] == "Fixed"
    assert normalized["files_changed"] == [
        {"path": "a.py", "operation": "modified", "previous_path": None}
    ]
    assert [call["status"] for call in normalized["tool_calls"]] == [
        "succeeded",
        "failed",
    ]
    assert "Secret internal reasoning" not in normalized["reasoning_summary"]
    assert normalized["reasoning_summary"] == "Reasoning occurred; omitted."
    assert normalized["timestamp_range"] == {
        "started_at": datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc),
        "ended_at": datetime(2026, 9, 19, 10, 0, 3, tzinfo=timezone.utc),
    }


def test_claude_task_window_keeps_implement_turn_not_setup_or_qa():
    source = "\n".join(
        [
            _line(
                {
                    "type": "user",
                    "sessionId": "s",
                    "timestamp": "2026-09-19T10:00:00Z",
                    "message": {"content": "plan the work"},
                }
            ),
            _line(
                {
                    "type": "assistant",
                    "sessionId": "s",
                    "timestamp": "2026-09-19T10:00:01Z",
                    "message": {"content": "I will start with a plan."},
                }
            ),
            _line(
                {
                    "type": "user",
                    "sessionId": "s",
                    "timestamp": "2026-09-19T10:01:00Z",
                    "message": {"content": "add the health endpoint"},
                }
            ),
            _line(
                {
                    "type": "assistant",
                    "sessionId": "s",
                    "timestamp": "2026-09-19T10:01:01Z",
                    "message": {
                        "content": [
                            {
                                "type": "thinking",
                                "thinking": "Secret internal reasoning details",
                            },
                            {
                                "type": "text",
                                "text": "The health-check endpoint is complete.",
                            },
                            {
                                "type": "tool_use",
                                "id": "edit-1",
                                "name": "Edit",
                                "input": {
                                    "file_path": "src/health.py",
                                    "old_string": "",
                                    "new_string": "def health(): return {'ok': True}",
                                },
                            },
                        ]
                    },
                }
            ),
            _line(
                {
                    "type": "user",
                    "sessionId": "s",
                    "timestamp": "2026-09-19T10:01:02Z",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "edit-1",
                                "content": "updated",
                            }
                        ]
                    },
                }
            ),
            _line(
                {
                    "type": "user",
                    "sessionId": "s",
                    "timestamp": "2026-09-19T11:00:00Z",
                    "message": {"content": "what branch is this?"},
                }
            ),
            _line(
                {
                    "type": "assistant",
                    "sessionId": "s",
                    "timestamp": "2026-09-19T11:00:01Z",
                    "message": {"content": "You are on phase-1-test."},
                }
            ),
        ]
    )

    normalized = normalize_events(parse_claude_jsonl(source))

    assert normalized["start_state"] == "add the health endpoint"
    assert normalized["end_state"] == "The health-check endpoint is complete."
    assert normalized["prompts"] == ["add the health endpoint"]
    assert normalized["files_changed"][0]["path"] == "src/health.py"
    assert "plan the work" not in normalized["prompts"]
    assert "You are on phase-1-test." not in normalized["end_state"]
    assert "Secret internal reasoning" not in normalized["reasoning_summary"]
    assert normalized["reasoning_summary"] == (
        "Reasoning occurred; omitted. Approach: "
        "The health-check endpoint is complete."
    )


@pytest.mark.parametrize(
    ("source", "code", "line_number"),
    [
        ("\n\n", IngestionErrorCode.NOTHING_TO_REPORT, None),
        (
            '{"type":"user","message":\n',
            IngestionErrorCode.MALFORMED_INPUT,
            1,
        ),
        (
            '\n{"type":"user","message":{"content":[{"type":"image"}]}}',
            IngestionErrorCode.UNSUPPORTED_EVENT,
            2,
        ),
    ],
)
def test_reports_stable_errors(source, code, line_number):
    with pytest.raises(IngestionError) as caught:
        parse_claude_jsonl(source)

    assert caught.value.code == code
    assert caught.value.line_number == line_number


def test_missing_timestamps_use_deterministic_epoch():
    normalized = normalize_events(
        parse_claude_jsonl(
            _line({"type": "user", "message": {"content": "hello"}})
        )
    )

    assert normalized["timestamp_range"]["started_at"] == datetime(
        1970, 1, 1, tzinfo=timezone.utc
    )
    assert (
        normalized["timestamp_range"]["ended_at"]
        == normalized["timestamp_range"]["started_at"]
    )
