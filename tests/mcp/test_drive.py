from pathlib import Path

from virtual_you.contracts.activity import ActivityRecord
from virtual_you.ingest.service import IngestionService
from virtual_you.ingest.store import ActivityRecordRepository
from virtual_you.mcp.drive import (
    DriveFile,
    FakeDriveClient,
    RestDriveClient,
    drive_enabled,
    enrich_drive,
)


FILE_URL = "https://docs.google.com/presentation/d/abc/edit"
FOLDER_ID = "folder-intern-docs"


def _record(**overrides) -> ActivityRecord:
    payload = {
        "schema_version": "1.0",
        "session_id": "session-1",
        "source": "claude",
        "start_state": "Working on the deck.",
        "prompts": ["Update the onboarding slides."],
        "reasoning_summary": "",
        "files_changed": [{"path": "app.py", "operation": "modified"}],
        "diffs": [],
        "tool_calls": [],
        "end_state": "Slides are ready.",
        "timestamp_range": {
            "started_at": "2026-09-19T01:00:00Z",
            "ended_at": "2026-09-19T02:00:00Z",
        },
        "redacted": True,
    }
    payload.update(overrides)
    return ActivityRecord.model_validate(payload)


def _file(**overrides) -> DriveFile:
    payload = dict(
        file_id="file-1",
        name="Onboarding.pptx",
        mime_type="application/vnd.google-apps.presentation",
        web_view_link=FILE_URL,
        modified_time="2026-09-19T01:30:00Z",
    )
    payload.update(overrides)
    return DriveFile(**payload)


def test_drive_flag_defaults_off(monkeypatch) -> None:
    monkeypatch.delenv("VIRTUAL_YOU_MCP_DRIVE", raising=False)
    assert drive_enabled() is False


def test_flag_off_never_calls_client() -> None:
    client = FakeDriveClient([_file()])
    record = _record()
    out = enrich_drive(record, client=client, enabled=False)
    assert client.calls == []
    assert out.tool_calls == record.tool_calls


def test_fake_file_in_range_becomes_drive_tool_call() -> None:
    record = _record()
    out = enrich_drive(record, client=FakeDriveClient([_file()]), enabled=True)
    names = {call.name: call.result_summary for call in out.tool_calls}
    assert "drive.file" in names
    assert FILE_URL in names["drive.file"]
    assert "Onboarding.pptx" in names["drive.file"]
    assert "application/vnd.google-apps.presentation" in names["drive.file"]
    ActivityRecord.model_validate(out.model_dump())


def test_file_outside_timestamp_range_is_omitted() -> None:
    client = FakeDriveClient(
        [_file(modified_time="2026-09-18T01:00:00Z")],
    )
    out = enrich_drive(_record(), client=client, enabled=True)
    assert all(call.name != "drive.file" for call in out.tool_calls)
    assert client.calls


def test_mock_file_bytes_are_ignored() -> None:
    secret_bytes = "FILE_BODY_SHOULD_NEVER_APPEAR"
    client = FakeDriveClient(
        [_file()],
        contents={"file-1": secret_bytes.encode("utf-8")},
    )
    out = enrich_drive(_record(), client=client, extra_secrets=[secret_bytes], enabled=True)
    dumped = out.model_dump_json()
    assert secret_bytes not in dumped
    assert FILE_URL in dumped
    assert "Onboarding.pptx" in dumped


def test_rest_client_ignores_content_field() -> None:
    def http_get(url, headers, timeout):
        assert "Bearer tok" in headers["Authorization"]
        assert "corpora=user" in url
        assert "supportsAllDrives" not in url
        return {
            "files": [
                {
                    "id": "file-1",
                    "name": "Onboarding.pptx",
                    "mimeType": "application/vnd.google-apps.presentation",
                    "webViewLink": FILE_URL,
                    "modifiedTime": "2026-09-19T01:30:00Z",
                    "content": "FILE_BODY_SHOULD_NEVER_APPEAR",
                    "body": b"bytes-here",
                }
            ]
        }

    client = RestDriveClient(FOLDER_ID, "tok", http_get=http_get)
    files = client.list_folder("2026-09-19T01:00:00Z", "2026-09-19T02:00:00Z")
    assert len(files) == 1
    assert files[0].name == "Onboarding.pptx"
    assert files[0].web_view_link == FILE_URL
    assert not hasattr(files[0], "content")
    dumped = files[0].__dict__
    assert "FILE_BODY_SHOULD_NEVER_APPEAR" not in str(dumped)


def test_secret_in_title_is_redacted() -> None:
    secret = "sk-live-drive-leak"
    out = enrich_drive(
        _record(),
        client=FakeDriveClient([_file(name="deck " + secret)]),
        extra_secrets=[secret],
        enabled=True,
    )
    dumped = out.model_dump_json()
    assert secret not in dumped
    assert "[REDACTED]" in dumped


def test_http_403_skips_files_and_keeps_session() -> None:
    def http_get(url, headers, timeout):
        return None

    client = RestDriveClient(FOLDER_ID, "tok", http_get=http_get)
    record = _record()
    out = enrich_drive(record, client=client, enabled=True)
    assert all(call.name != "drive.file" for call in out.tool_calls)
    assert out.session_id == record.session_id


def test_rest_from_env_requires_folder_and_token() -> None:
    assert RestDriveClient.from_env({}) is None
    assert RestDriveClient.from_env({"VIRTUAL_YOU_DRIVE_FOLDER_ID": FOLDER_ID}) is None
    client = RestDriveClient.from_env(
        {
            "VIRTUAL_YOU_DRIVE_FOLDER_ID": FOLDER_ID,
            "GOOGLE_ACCESS_TOKEN": "tok",
        }
    )
    assert client is not None
    assert client.folder_id == FOLDER_ID


def test_ingest_with_injected_drive_client(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VIRTUAL_YOU_MCP_DRIVE", "true")
    session = tmp_path / "session.jsonl"
    session.write_text(
        '{"type":"user","sessionId":"s1","timestamp":"2026-09-19T01:00:00Z",'
        '"message":{"content":"Update the onboarding slides."}}\n',
        encoding="utf-8",
    )
    service = IngestionService(
        ActivityRecordRepository(tmp_path / "activities"),
        data_directory=tmp_path,
        env_search_root=tmp_path,
        workspace_root=tmp_path,
        apply_git_overlay=False,
        drive_client=FakeDriveClient([_file(modified_time="2026-09-19T01:00:00Z")]),
    )
    record = service.ingest_file("claude", session)
    names = {call.name: call.result_summary for call in record.tool_calls}
    assert FILE_URL in names["drive.file"]
