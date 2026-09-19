import json
import time
from pathlib import Path

import httpx
from conftest import KEY, approve, new_draft, persona_payload
from fastapi.testclient import TestClient

from virtual_you.backend.app import create_app
from virtual_you.contracts.activity import ActivityRecord
from virtual_you.ingest import IngestionService
from virtual_you.ingest.store import ActivityRecordRepository


def test_real_ingestion_flows_through_to_review_and_delivery(client, settings, tmp_path):
    source = Path(__file__).parents[1] / "fixtures" / "claude_session.jsonl"
    record = IngestionService(
        data_directory=settings.data_dir, workspace_root=tmp_path, env_secrets=()
    ).ingest_file("claude", source)
    refreshed = client.post("/api/refresh").json()
    assert refreshed["changed"] == 1
    client.post("/api/personas", json=persona_payload())
    draft = new_draft(client)
    assert any(e["session_id"] == record.session_id for e in draft["evidence"])
    draft = approve(client, draft)
    result = client.post(f"/api/drafts/{draft['id']}/deliver", json={"expected_revision": 1}).json()
    assert result["status"] == "simulated"


def test_refresh_is_incremental_and_changes_replace_search_results(client, record, settings):
    repository = ActivityRecordRepository(settings.activity_dir)
    repository.save(record)
    assert client.post("/api/refresh").json()["changed"] == 1
    assert client.post("/api/refresh").json()["unchanged"] == 1
    record["end_state"] = "Zebracorn search term"
    repository.save(record)
    assert client.post("/api/refresh").json()["changed"] == 1
    hits = client.post("/api/retrieval/search", json={"query": "Zebracorn"}).json()["matches"]
    assert len(hits) == 1
    record["end_state"] = "New replacement"
    repository.save(record)
    client.post("/api/refresh")
    assert client.post("/api/retrieval/search", json={"query": "Zebracorn"}).json()["matches"] == []
    assert client.get("/api/status").json()["activity"]["count"] == 1


def test_invalid_file_is_isolated_and_deletion_removes_index(client, record, settings):
    ActivityRecordRepository(settings.activity_dir).save(record)
    invalid = settings.activity_dir / "activity-broken.json"
    invalid.write_text('{"token":"secret-and-partial')
    refreshed = client.post("/api/refresh").json()
    assert refreshed["changed"] == 1 and refreshed["state"] == "degraded"
    assert "secret-and-partial" not in json.dumps(refreshed)
    for path in settings.activity_dir.glob("activity-*.json"):
        path.unlink()
    assert client.post("/api/refresh").json()["removed"] == 1
    assert client.get("/api/status").json()["activity"]["count"] == 0


def test_invalid_replacement_removes_previously_valid_record(client, record, settings):
    ActivityRecordRepository(settings.activity_dir).save(record)
    client.post("/api/refresh")
    next(settings.activity_dir.glob("activity-*.json")).write_text("not valid json")
    assert client.post("/api/refresh").json()["removed"] == 1
    assert client.post("/api/retrieval/search", json={}).json()["matches"] == []


def test_only_explicitly_redacted_contracts_accepted_and_rechecked(client, record):
    unmarked = dict(record)
    del unmarked["redacted"]
    assert client.post("/api/activities", json=unmarked).status_code == 422
    record["redacted"] = False
    assert client.post("/api/activities", json=record).status_code == 422
    record["redacted"] = True
    record["end_state"] = "API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    assert client.post("/api/activities", json=record).status_code == 201
    hits = client.post("/api/retrieval/search", json={}).json()["matches"]
    assert "sk-proj-" not in json.dumps(hits)


def test_search_scopes_sessions_and_dates_without_fts_injection(client, record):
    client.post("/api/activities", json=record)
    assert client.post("/api/retrieval/search", json={"query": 'payment" OR *'}).status_code == 200
    assert (
        client.post("/api/retrieval/search", json={"session_ids": ["different"]}).json()["matches"]
        == []
    )
    assert (
        client.post("/api/retrieval/search", json={"since": "2027-01-01T00:00:00Z"}).json()[
            "matches"
        ]
        == []
    )
    assert (
        client.post(
            "/api/retrieval/search",
            json={"since": "2027-01-01T00:00:00Z", "until": "2026-01-01T00:00:00Z"},
        ).status_code
        == 422
    )
    assert client.post("/api/retrieval/search", json={"query": "!!!"}).json()["matches"] == []


def test_heartbeat_periodically_discovers_records(settings, record):
    settings.heartbeat_enabled, settings.heartbeat_seconds = True, 1
    with TestClient(create_app(settings)) as client:
        client.headers["Authorization"] = "Bearer " + KEY
        ActivityRecordRepository(settings.activity_dir).save(record)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if client.get("/api/status").json()["activity"]["count"] == 1:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("Heartbeat did not index the new record")
        assert client.get("/api/drafts").json() == []


def test_remote_feed_uses_authenticated_get_and_reports_failure(settings, record):
    settings.activity_feed_url = "https://ingestion.example/activities"
    settings.activity_feed_token = "feed-test-token"
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=[record]) if len(calls) == 1 else httpx.Response(503)

    with TestClient(create_app(settings, transport=httpx.MockTransport(handler))) as client:
        client.headers["Authorization"] = "Bearer " + KEY
        assert client.get("/api/status").json()["activity"]["count"] == 1
        assert calls[0].method == "GET"
        assert calls[0].headers["Authorization"] == "Bearer feed-test-token"
        status = client.post("/api/refresh").json()
        assert status["state"] == "degraded" and status["last_success_at"]
        assert client.get("/api/status").json()["activity"]["count"] == 1


def test_same_session_from_different_source_is_rejected(client, record):
    client.post("/api/activities", json=record)
    record["source"] = "cursor"
    assert client.post("/api/activities", json=record).status_code == 409


def test_empty_activity_not_indexed(client, record):
    empty = ActivityRecord(
        session_id="empty", source="claude", timestamp_range=record["timestamp_range"]
    )
    assert client.post("/api/activities", json=empty.model_dump(mode="json")).status_code == 422


def test_delayed_feed_record_does_not_rollback_newer_session(client, record):
    client.post("/api/activities", json=record)
    record["timestamp_range"]["ended_at"] = "2026-09-19T01:01:00Z"
    record["end_state"] = "An older stale observation"
    assert client.post("/api/activities", json=record).json()["changed"] is False
    matches = client.post("/api/retrieval/search", json={}).json()["matches"]
    assert "An older stale observation" not in str(matches)


def test_missing_activity_mount_preserves_index_and_marks_degraded(client, record, settings):
    ActivityRecordRepository(settings.activity_dir).save(record)
    client.post("/api/refresh")
    settings.activity_dir.rename(settings.activity_dir.with_name("temporarily-offline"))
    status = client.post("/api/refresh").json()
    assert status["state"] == "degraded" and status["count"] == 1
    assert status["errors"][0]["code"] == "directory_unavailable"


def test_project_subfolders_assign_future_records_and_filters_deny_empty(client, settings, record):
    import asyncio
    import json

    from virtual_you.contracts.reporting import RetrievalRequest

    project = settings.activity_dir / "Team project"
    project.mkdir()
    (project / "activity-one.json").write_text(json.dumps(record))
    asyncio.run(client.app.state.heartbeat.refresh())
    retrieval = client.app.state.retrieval
    assert retrieval.project_choices() == ["Team project"]
    assert retrieval.search(
        RetrievalRequest(project_ids=["Team project"], sources=[record["source"]])
    )
    assert not retrieval.search(RetrievalRequest(project_ids=[]))
    assert not retrieval.search(RetrievalRequest(sources=[]))
    assert not retrieval.search(RetrievalRequest(project_ids=["Other project"]))
