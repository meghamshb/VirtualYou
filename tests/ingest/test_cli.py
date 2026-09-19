import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from virtual_you.cli import main
from virtual_you.ingest.errors import IngestionError, IngestionErrorCode


FIXTURES = Path(__file__).parents[1] / "fixtures"


class SuccessfulService:
    def ingest(self, *, path: Path, source: str) -> dict:
        return {
            "path": str(path),
            "source": source,
            "redacted": True,
        }


class FailingService:
    def ingest(self, *, path: Path, source: str) -> dict:
        raise IngestionError(
            IngestionErrorCode.MALFORMED_INPUT,
            "Input is malformed.",
            line_number=3,
        )


class RecordingService:
    def __init__(self) -> None:
        self.calls = []

    def ingest(self, *, path: Path, source: str) -> dict:
        self.calls.append(("ingest", path, source))
        return {"path": str(path), "source": source, "redacted": True}

    def watch(self, *, path: Path, source: str) -> dict:
        self.calls.append(("watch", path, source))
        return {"path": str(path), "source": source}


def test_cli_prints_machine_readable_activity(capsys) -> None:
    result = main(
        ["ingest", "--source", "claude", "session.jsonl"],
        service=SuccessfulService(),
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "path": "session.jsonl",
        "redacted": True,
        "source": "claude",
    }


def test_cli_prints_stable_secret_safe_error(capsys) -> None:
    result = main(
        ["ingest", "--source", "claude", "session.jsonl"],
        service=FailingService(),
    )

    assert result == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload == {
        "code": "malformed_input",
        "line_number": 3,
        "message": "Input is malformed.",
    }


def test_cli_exports_activity_json_schema(capsys) -> None:
    result = main(["schema"], service=SuccessfulService())

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["title"] == "ActivityRecord"
    assert payload["properties"]["redacted"]["const"] is True


def test_cli_ingest_latest_resolves_discovered_path(tmp_path: Path, capsys) -> None:
    session = tmp_path / "newest.jsonl"
    session.write_text("{}\n", encoding="utf-8")
    service = RecordingService()

    result = main(
        ["ingest", "--source", "cursor", "--latest"],
        service=service,
        discover=lambda source: session,
    )

    assert result == 0
    assert service.calls == [("ingest", session, "cursor")]
    payload = json.loads(capsys.readouterr().out)
    assert payload["path"] == str(session)
    assert payload["source"] == "cursor"


def test_cli_watch_latest_resolves_discovered_path(tmp_path: Path) -> None:
    session = tmp_path / "live.jsonl"
    session.write_text("{}\n", encoding="utf-8")
    service = RecordingService()

    result = main(
        ["watch", "--source", "codex", "--latest"],
        service=service,
        discover=lambda source: session,
    )

    assert result == 0
    assert service.calls == [("watch", session, "codex")]


def test_cli_explicit_path_wins_over_latest(tmp_path: Path) -> None:
    discovered = tmp_path / "discovered.jsonl"
    explicit = tmp_path / "explicit.jsonl"
    discovered.write_text("{}\n", encoding="utf-8")
    explicit.write_text("{}\n", encoding="utf-8")
    service = RecordingService()

    result = main(
        ["ingest", "--source", "claude", "--latest", str(explicit)],
        service=service,
        discover=lambda source: discovered,
    )

    assert result == 0
    assert service.calls == [("ingest", explicit, "claude")]


def test_cli_ingest_requires_path_or_latest() -> None:
    with pytest.raises(SystemExit) as error:
        main(["ingest", "--source", "claude"], service=SuccessfulService())

    assert error.value.code == 2


def test_cli_ingest_latest_rejects_voice(capsys) -> None:
    result = main(["ingest", "--source", "voice", "--latest"], service=SuccessfulService())

    assert result == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["code"] == "unsupported_event"


def test_cli_data_dir_persists_ingested_activity(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    monkeypatch.setenv("VIRTUAL_YOU_GIT_ROOT", str(tmp_path))
    result = main(
        [
            "--data-dir",
            str(tmp_path),
            "ingest",
            "--source",
            "claude",
            str(FIXTURES / "claude_session.jsonl"),
        ]
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["redacted"] is True
    assert payload["source"] == "claude"
    assert payload["source_path"] == str((FIXTURES / "claude_session.jsonl").resolve())
    assert list((tmp_path / "activities").glob("activity-*.json"))


def test_cli_watch_ingests_appended_jsonl(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    monkeypatch.setenv("VIRTUAL_YOU_GIT_ROOT", str(tmp_path))
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
    data = tmp_path / "data"

    first_result = main(
        ["--data-dir", str(data), "watch", "--source", "claude", str(source)]
    )
    first_payload = json.loads(capsys.readouterr().out)
    assert first_result == 0
    assert first_payload["prompts"] == ["Fix callback validation."]
    assert first_payload["source_path"] == str(source.resolve())

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

    second_result = main(
        ["--data-dir", str(data), "watch", "--source", "claude", str(source)]
    )
    second_payload = json.loads(capsys.readouterr().out)
    assert second_result == 0
    assert second_payload["end_state"] == "Callback validation is complete."
    assert second_payload["prompts"] == ["Fix callback validation."]

    idle = main(
        ["--data-dir", str(data), "watch", "--source", "claude", str(source)]
    )
    idle_output = capsys.readouterr().out
    assert idle == 0
    assert idle_output == ""


def test_python_module_entry_runs_schema() -> None:
    env = os.environ.copy()
    src = Path(__file__).resolve().parents[2] / "src"
    env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "-m", "virtual_you", "schema"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["title"] == "ActivityRecord"
