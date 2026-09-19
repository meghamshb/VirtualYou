import json
from pathlib import Path

from virtual_you.cli import main
from virtual_you.ingest.errors import IngestionError, IngestionErrorCode


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
