import json
from datetime import timezone
from pathlib import Path

import pytest

from virtual_you.ingest.codex import parse_codex_file, parse_codex_jsonl
from virtual_you.ingest.errors import IngestionError, IngestionErrorCode
from virtual_you.ingest.events import (
    AssistantTextEvent,
    FileChangeEvent,
    ReasoningEvent,
    SessionEndEvent,
    ToolCallEvent,
    UserPromptEvent,
)
from virtual_you.ingest.normalize import normalize_events


FIXTURE = Path(__file__).parents[1] / "fixtures" / "codex_session.jsonl"


def test_parse_codex_rollout_extracts_prompts_tools_and_patches():
    events = parse_codex_file(FIXTURE)

    prompt = next(event for event in events if isinstance(event, UserPromptEvent))
    assert prompt.text == "Create a Dockerfile"
    assert prompt.session_id == "codex-demo"
    assert prompt.timestamp.tzinfo == timezone.utc
    assert any(isinstance(event, ReasoningEvent) for event in events)
    assert any(
        isinstance(event, AssistantTextEvent) and event.text == "Dockerfile added."
        for event in events
    )
    call = next(event for event in events if isinstance(event, ToolCallEvent))
    assert call.name == "exec"
    assert call.call_id == "call-1"
    changes = [event for event in events if isinstance(event, FileChangeEvent)]
    assert [(change.operation, change.path) for change in changes] == [
        ("added", "Dockerfile"),
        ("modified", "README.md"),
    ]
    assert any(isinstance(event, SessionEndEvent) for event in events)
    assert not any(
        isinstance(event, UserPromptEvent) and "app-context" in event.text
        for event in events
    )


def test_malformed_codex_json_has_stable_line_number():
    with pytest.raises(IngestionError) as error:
        parse_codex_jsonl('{"type": "session_meta", "payload": {}}\n{\n')

    assert error.value.code is IngestionErrorCode.MALFORMED_INPUT
    assert error.value.line_number == 2


def test_unknown_codex_event_fails_loudly():
    with pytest.raises(IngestionError) as error:
        parse_codex_jsonl(json.dumps({"type": "future_event", "payload": {}}))

    assert error.value.code is IngestionErrorCode.UNSUPPORTED_EVENT
    assert error.value.line_number == 1


def test_codex_task_window_keeps_last_file_change_turn():
    events = [
        UserPromptEvent(line_number=1, text="plan the work", session_id="codex-1"),
        AssistantTextEvent(line_number=2, text="I will start with a plan."),
        UserPromptEvent(line_number=3, text="Create a Dockerfile", session_id="codex-1"),
        FileChangeEvent(
            line_number=4,
            path="Dockerfile",
            operation="added",
            diff="FROM python:3.12",
        ),
        AssistantTextEvent(line_number=5, text="Dockerfile added."),
        UserPromptEvent(line_number=6, text="what next?"),
        AssistantTextEvent(line_number=7, text="Deploy it."),
    ]

    normalized = normalize_events(events, source="codex")

    assert normalized["prompts"] == ["Create a Dockerfile"]
    assert normalized["start_state"] == "Create a Dockerfile"
    assert normalized["end_state"] == "Dockerfile added."
    assert normalized["files_changed"][0]["path"] == "Dockerfile"
    assert normalized["reasoning_summary"] == "Dockerfile added."
    assert "Deploy it." not in normalized["end_state"]
