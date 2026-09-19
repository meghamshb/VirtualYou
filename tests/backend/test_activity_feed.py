import json
from types import SimpleNamespace

import pytest
from conftest import approve, new_draft, prepare

from virtual_you.backend.collection import Collector


def test_activity_requires_authentication_and_bounds_limit(client):
    assert client.get("/api/activity", headers={"Authorization": "Bearer wrong"}).status_code == 401
    for limit in (0, -1, 101, "many"):
        assert client.get(f"/api/activity?limit={limit}").status_code == 422
    response = client.get("/api/activity")
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["collection"]["state"] == "empty"
    assert response.json()["collection"]["configured"] is False


def test_activity_combines_normalized_work_and_draft_decisions_without_private_payloads(
    client, record
):
    record.update(
        source_path="/private/raw-sessions/test.jsonl", end_state="Completed the callback check."
    )
    record["timestamp_range"] = {
        "started_at": "2020-01-01T00:00:00Z",
        "ended_at": "2020-01-01T00:01:00Z",
    }
    prepare(client, record)
    client.app.state.retrieval.assign_project([record["session_id"]], "demo")
    draft = approve(client, new_draft(client))
    response = client.get("/api/activity").json()
    assert [item["kind"] for item in response["items"]] == [
        "draft_event",
        "draft_event",
        "activity",
    ]
    decision, created, activity = response["items"]
    assert decision["title"] == "Draft approved" and decision["action"] == "approve"
    assert decision["draft_id"] == draft["id"] and decision["revision"] == 1
    assert created["action"] == "created"
    assert activity["source"] == record["source"]
    assert activity["summary"] == record["end_state"]
    assert activity["project_id"] == "demo"
    assert activity["files_changed_count"] == len(record["files_changed"])
    wire = json.dumps(response)
    for private in (
        "/private/raw-sessions",
        "source_path",
        "prompts",
        "diffs",
        "input_summary",
        "soul_md",
        "approval",
        "detail",
    ):
        assert private not in wire
    limited = client.get("/api/activity?limit=2").json()
    assert len(limited["items"]) == 2 and limited["has_more"] is True
    assert response["has_more"] is False


def test_activity_redacts_stored_summary_again_and_caps_output(client, record):
    client.post("/api/activities", json=record)
    # Defensive projection of a legacy/imported record; no real credential is used.
    secret = "sk-proj-syntheticabcdefghijklmnopqrstuvwxyz123456"
    record["end_state"] = "API_KEY=" + secret + " " + "Long summary. " * 200
    with client.app.state.store.connection(write=True) as db:
        db.execute("UPDATE activities SET payload=?", (json.dumps(record),))
    result = client.get("/api/activity").json()
    assert secret not in json.dumps(result)
    assert "[REDACTED]" in result["items"][0]["summary"]
    assert len(result["items"][0]["summary"]) == 600


def test_empty_source_is_not_reported_as_a_parse_failure(client, settings, tmp_path):
    source = tmp_path / "empty.jsonl"
    source.write_text("", encoding="utf-8")
    config = tmp_path / "ingestion.json"
    config.write_text(
        json.dumps(
            [
                {
                    "source": "claude",
                    "path": str(source),
                    "workspace": str(tmp_path),
                    "project": "demo",
                }
            ]
        )
    )
    settings.ingestion_config = config
    result = client.post("/api/refresh").json()
    assert result["state"] == "healthy" and result["errors"] == []
    summary = client.get("/api/status").json()["collection"]
    assert summary["state"] == "empty" and summary["configured"] is True
    assert summary["empty_inputs"] == 1
    source.write_text("this is not valid json", encoding="utf-8")
    client.post("/api/refresh")
    summary = client.get("/api/activity").json()["collection"]
    assert summary["state"] == "degraded" and summary["record_count"] == 0
    assert summary["errors"][0]["code"] == "source_ingestion_failed"
    assert str(tmp_path) not in json.dumps(summary)


def test_empty_source_list_is_not_claimed_as_configured(client, settings, tmp_path):
    config = tmp_path / "sources.json"
    config.write_text("[]")
    settings.ingestion_config = config
    client.post("/api/refresh")
    summary = client.get("/api/status").json()["collection"]
    assert summary["state"] == "empty" and summary["configured"] is False
    config.write_text("{invalid private config")
    client.post("/api/refresh")
    summary = client.get("/api/status").json()["collection"]
    assert summary["state"] == "degraded"
    assert summary["errors"][0]["code"] == "invalid_ingestion_config"
    assert "private config" not in json.dumps(summary)


def test_unexpected_refresh_failure_replaces_old_health_without_exposing_exception(
    client, monkeypatch
):
    previous = client.get("/api/status").json()["collection"]["last_success_at"]

    def fail(_):
        raise RuntimeError("private source filename and API_KEY=synthetic-secret")

    monkeypatch.setattr(Collector, "collect", fail)
    refresh = client.post("/api/refresh")
    assert refresh.status_code == 200
    assert refresh.json()["state"] == "failed"
    status = client.get("/api/status")
    assert status.status_code == 200  # Backend connectivity remains healthy.
    collection = status.json()["collection"]
    assert collection["state"] == "failed"
    assert collection["last_success_at"] == previous
    assert collection["error_count"] == 1
    assert "private source" not in status.text and "synthetic-secret" not in status.text


def test_workflow_pause_requires_auth_and_running_coordinator(client):
    assert (
        client.post(
            "/api/workflow/pause", json={"paused": True}, headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    assert client.get("/api/status").json()["workflow"] == {"available": False, "paused": False}
    result = client.post("/api/workflow/pause", json={"paused": True})
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "workflow_unavailable"
    assert client.app.state.store.metadata("slack_preferences") is None


def test_workflow_pause_is_unavailable_after_worker_stops(client):
    client.app.state.slack_coordinator = SimpleNamespace()
    client.app.state.slack_worker = SimpleNamespace(done=lambda: True)
    assert client.get("/api/status").json()["workflow"]["available"] is False
    assert client.post("/api/workflow/pause", json={"paused": True}).status_code == 409
    assert client.app.state.store.metadata("slack_preferences") is None


def test_collection_counts_all_errors_while_bounding_details(client, settings, tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    for index in range(25):
        (sources / f"private-name-{index}.jsonl").write_text("invalid json")
    config = tmp_path / "ingestion.json"
    config.write_text(
        json.dumps(
            [
                {
                    "source": "claude",
                    "path": str(sources),
                    "workspace": str(tmp_path),
                    "project": "demo",
                }
            ]
        )
    )
    settings.ingestion_config = config
    client.post("/api/refresh")
    summary = client.get("/api/status").json()["collection"]
    assert summary["error_count"] == 25 and len(summary["errors"]) == 20
    assert summary["state"] == "degraded"
    assert "private-name" not in json.dumps(summary)


@pytest.mark.parametrize(
    "payload", [{"paused": "false"}, {"paused": 1}, {}, {"paused": True, "sources": []}]
)
def test_workflow_pause_contract_is_strict(client, payload):
    client.app.state.slack_coordinator = SimpleNamespace()
    assert client.post("/api/workflow/pause", json=payload).status_code == 422
    assert client.app.state.store.metadata("slack_preferences") is None


def test_pause_preserves_preferences_and_blocks_existing_linked_draft(client, record):
    prepare(client, record)
    draft = approve(client, new_draft(client))
    client.app.state.slack_coordinator = SimpleNamespace()
    store = client.app.state.store
    prefs = {"sources": ["git", "voice"], "paused": False, "future_setting": {"keep": True}}
    store.set_metadata("slack_preferences", prefs)
    with store.connection(write=True) as db:
        db.executescript("""
            CREATE TABLE slack_drafts (draft_id TEXT PRIMARY KEY, recipient TEXT);
            CREATE TABLE slack_recipients (recipient TEXT PRIMARY KEY, payload TEXT);
        """)
        db.execute("INSERT INTO slack_drafts VALUES (?,?)", (draft["id"], "colleague"))
    assert client.post("/api/workflow/pause", json={"paused": True}).json() == {
        "available": True,
        "paused": True,
    }
    assert store.metadata("slack_preferences") == {**prefs, "paused": True}
    assert client.get("/api/status").json()["workflow"] == {"available": True, "paused": True}
    blocked = client.post(f"/api/drafts/{draft['id']}/deliver", json={"expected_revision": 1})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "workflow_paused"
    assert store.get_draft(draft["id"])["status"] == "approved"
    assert client.post("/api/workflow/pause", json={"paused": False}).status_code == 200
    assert store.metadata("slack_preferences") == prefs


def test_configuration_status_uses_owner_installation_without_claiming_live_verification(
    client, monkeypatch
):
    for key in (
        "SLACK_CLIENT_ID",
        "SLACK_CLIENT_SECRET",
        "SLACK_REDIRECT_URI",
        "GITHUB_TOKEN",
        "VIRTUAL_YOU_GITHUB_REPO",
        "JIRA_API_TOKEN",
        "JIRA_BASE_URL",
        "JIRA_EMAIL",
        "GOOGLE_ACCESS_TOKEN",
        "VIRTUAL_YOU_DRIVE_FOLDER_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    status = client.get("/api/status").json()
    assert all(value["status"] == "not_configured" for value in status["integrations"].values())
    assert status["integrations"]["slack"]["oauth_installed"] is False
    credentials = SimpleNamespace(
        installation=lambda: SimpleNamespace(
            user_token="test-user-token", bot_token="test-bot-token"
        ),
        user_token=lambda: "test-user-token",
        bot_token=lambda: "test-bot-token",
    )
    client.app.state.slack_coordinator = SimpleNamespace(credentials=credentials)
    status = client.get("/api/status")
    slack = status.json()["integrations"]["slack"]
    assert slack["status"] == "configured" and slack["oauth_installed"] is True
    assert slack["verification"] == "not_checked"
    assert "test-user-token" not in status.text and "test-bot-token" not in status.text
    assert "connected" not in slack
