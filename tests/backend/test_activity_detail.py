import json

import pytest

from virtual_you.backend.collection import Collector


def test_detail_authentication_redaction_provenance_and_real_source_counts(client, record):
    record.update(
        source="codex", session_id="/Users/private-user/raw/session-1",
        source_path="/private/raw-secret-session.jsonl",
        prompts=["Fix the callback using API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz123456"],
        reasoning_summary="Validation was added after the callback failed.",
        files_changed=[{"path": "/Users/private-user/work/app.py", "operation": "modified"}],
        tool_calls=[{
            "call_id": "event-1", "name": "exec_command", "input_summary": "pytest",
            "result_summary": "3 passed; password=synthetic-password",
            "status": "succeeded", "timestamp": "2026-09-20T01:00:00Z",
        }],
    )
    client.post("/api/activities", json=record)
    # Resolve project using the stored normalized ID, not the display-safe URL.
    client.app.state.retrieval.assign_project([record["session_id"]], "test-project")
    feed = client.get("/api/activity").json()
    activity_id = feed["items"][0]["id"]
    url = "/api/activity/" + activity_id
    assert client.get(url, headers={"Authorization": "Bearer invalid"}).status_code == 401
    response = client.get(url)
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["id"] == activity_id and detail["source"] == "codex"
    assert detail["project_id"] == "test-project"
    assert detail["ingested_at"] and detail["started_at"] and detail["ended_at"]
    assert detail["redacted"] and not detail["truncated"]
    assert detail["tool_calls"][0]["result_summary"] == "3 passed; password=[REDACTED]"
    assert detail["tool_calls"][0]["timestamp"] == "2026-09-20T01:00:00+00:00"
    assert detail["files_changed"] == [{"path": "[local]/app.py", "operation": "modified"}]
    assert detail["session_id"] == "[local]/session-1"
    for private in ("source_path", "raw-secret-session", "private-user", "sk-proj-", "synthetic-password"):
        assert private not in response.text
    counts = {item["source"]: item for item in feed["source_counts"]}
    assert counts["codex"]["count"] == 1 and counts["claude"]["count"] == 0
    assert not counts["codex"]["configured"]  # API import is not a configured log collector.
    assert counts["codex"]["scan_state"] == "not_started"
    assert sum(item["count"] for item in counts.values()) == 1


def test_private_filesystem_paths_are_hidden_without_corrupting_routes_or_links(client, record):
    record["prompts"] = [
        'Read /private/tmp/private-user/session.jsonl and /opt/private-project/app.py. '
        'Use "/Users/Private Person/project/file name.py" then C:\\Users\\Someone\\repo\\app.py. '
        'Call /api/refresh or https://example.test/private/path?issue=1.',
    ]
    client.post("/api/activities", json=record)
    key = client.get("/api/activity").json()["items"][0]["id"]
    detail = client.get("/api/activity/" + key).json()
    prompt = detail["prompts"][0]
    for hidden in ("private-user", "private-project", "Private Person", "Someone", "/private/tmp", "/opt/"):
        assert hidden not in prompt
    assert "[local]/session.jsonl" in prompt and '"[local]/file name.py"' in prompt
    assert "/api/refresh" in prompt
    assert "https://example.test/private/path?issue=1" in prompt


@pytest.mark.parametrize("identifier", ["activity:0", "activity:-1", "activity:999999", "draft-event:1", "session-1", "activity:1'OR'1"])
def test_detail_requires_existing_opaque_activity_id(client, identifier):
    response = client.get("/api/activity/" + identifier)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "activity_not_found"


def test_detail_total_budget_and_item_caps_redact_before_truncation(client, record):
    record.update(
        prompts=["password=synthetic-password " + "a " * 2000 for _ in range(30)],
        diffs=["patch " * 500 for _ in range(30)],
        tool_calls=[{
            "call_id": str(n), "name": "exec", "input_summary": "input " * 1000,
            "result_summary": "output " * 500,
        } for n in range(40)],
        files_changed=[{"path": f"file{n}.py", "operation": "modified"} for n in range(100)],
    )
    imported = client.post("/api/activities", json=record)
    assert imported.status_code == 201, imported.text
    key = client.get("/api/activity").json()["items"][0]["id"]
    detail = client.get("/api/activity/" + key).json()
    assert detail["truncated"]
    assert len(detail["prompts"]) == 8 and len(detail["diffs"]) == 5
    assert len(detail["tool_calls"]) == 20 and len(detail["files_changed"]) == 40
    assert "synthetic-password" not in json.dumps(detail)
    assert len(json.dumps(detail)) < 35_000
    assert detail["tool_calls"][0]["timestamp"] is None


def configure_sources(settings, tmp_path):
    workspace, other = tmp_path / "project", tmp_path / "other"
    workspace.mkdir()
    other.mkdir()
    sessions, claude = tmp_path / "sessions", tmp_path / "claude-project"
    sessions.mkdir()
    claude.mkdir()
    config = tmp_path / "ingestion.json"
    config.write_text(json.dumps([
        {"source": "codex", "path": str(sessions), "workspace": str(workspace),
         "project": "demo", "pattern": "**/*.jsonl", "match_workspace": True},
        {"source": "claude", "path": str(claude), "workspace": str(workspace), "project": "demo"},
    ]) + "\n")
    settings.ingestion_config = config
    return workspace, other, sessions


def write_codex(path, workspace, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in [
        {"type": "session_meta", "payload": {"id": path.stem, "cwd": str(workspace)}},
        {"type": "response_item", "timestamp": "2026-09-20T01:00:00Z", "payload": {
            "type": "message", "role": "user", "content": text,
        }},
    ]) + "\n")


def test_codex_workspace_filter_new_file_discovery_and_configured_empty_source(client, settings, tmp_path, monkeypatch):
    from virtual_you.backend import collection

    workspace, other, sessions = configure_sources(settings, tmp_path)
    accepted, rejected = sessions / "2026/accepted.jsonl", sessions / "2026/rejected.jsonl"
    write_codex(accepted, workspace, "Implement callback validation.")
    write_codex(rejected, other, "Private unrelated work must not be read.")
    parsed = []
    original = collection.public_session_chunks

    def scoped(path):
        parsed.append(path)
        assert path != rejected  # Filtering happens before any public chunks/raw log body is read.
        yield from original(path)

    monkeypatch.setattr(collection, "public_session_chunks", scoped)
    result = client.post("/api/refresh").json()
    assert result["collection"]["changed"] == 1 and result["state"] == "healthy", result
    assert parsed == [accepted]
    feed = client.get("/api/activity").json()
    counts = {row["source"]: row for row in feed["source_counts"]}
    assert counts["codex"]["configured"] and counts["codex"]["scan_state"] == "healthy"
    assert counts["codex"]["count"] == 1 and counts["codex"]["last_scan_at"]
    assert counts["claude"]["configured"] and counts["claude"]["scan_state"] == "empty"
    assert counts["claude"]["count"] == 0 and counts["claude"]["latest_at"] is None
    assert not counts["git"]["configured"]
    detail = client.get("/api/activity/" + feed["items"][0]["id"]).json()
    assert detail["prompts"] == ["Implement callback validation."]
    write_codex(sessions / "2026/later.jsonl", workspace, "Add a validation test.")
    later = client.post("/api/refresh").json()
    assert later["collection"]["changed"] == 1 and later["collection"]["unchanged"] == 1
    assert not any("Private unrelated" in json.dumps(record) for record in client.post("/api/retrieval/search", json={}).json()["matches"])


def test_invalid_or_oversized_codex_metadata_is_an_error_not_an_empty_success(settings, tmp_path):
    settings.prepare()
    _, _, sessions = configure_sources(settings, tmp_path)
    (sessions / "broken.jsonl").write_text('{"type":"event_msg"}\n')
    (sessions / "oversized.jsonl").write_text(" " * 1_000_001)
    result = Collector(settings).collect()
    assert result["changed"] == 0 and result["error_count"] == 2
    sources = {row["source"]: row for row in result["sources"]}
    assert sources["codex"]["configured"] and sources["codex"]["scan_state"] == "failed"
    assert sources["claude"]["scan_state"] == "empty"
