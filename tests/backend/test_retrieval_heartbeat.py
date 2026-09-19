import json
import time
from pathlib import Path

import httpx
from conftest import KEY, approve, new_draft, persona_payload
from fastapi.testclient import TestClient

from virtual_you.backend.app import create_app
from virtual_you.backend.providers import UNKNOWN, DemoProvider
from virtual_you.contracts.activity import ActivityRecord
from virtual_you.ingest import IngestionService
from virtual_you.ingest.store import ActivityRecordRepository
from virtual_you.mcp.jira import FakeJiraClient, JiraIssue


def test_real_ingestion_flows_through_to_review_and_delivery(client, settings):
    source = Path(__file__).parents[1] / "fixtures" / "claude_session.jsonl"
    record = IngestionService(
        data_directory=settings.data_dir,
        workspace_root=settings.data_dir,
        apply_git_overlay=False,
    ).ingest_file("claude", source)
    refreshed = client.post("/api/refresh").json()
    assert refreshed["changed"] == 1
    client.post("/api/personas", json=persona_payload())
    draft = new_draft(client)
    assert any(e["session_id"] == record.session_id for e in draft["evidence"])
    draft = approve(client, draft)
    result = client.post(f"/api/drafts/{draft['id']}/deliver", json={"expected_revision": 1}).json()
    assert result["status"] == "simulated"


def test_jira_evidence_flows_to_approved_slack_payload(
    settings,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("VIRTUAL_YOU_MCP_JIRA", "true")
    settings.prepare()
    settings.live_delivery = True
    settings.slack_bot_token = "test-token"
    settings.slack_channels = ("demo-channel",)
    issue = JiraIssue(
        key="ENG-184",
        summary="Fix overlay",
        status="In Progress",
        status_category="indeterminate",
        updated="2026-09-19T01:05:00Z",
        url="https://example.atlassian.net/browse/ENG-184",
    )
    session = tmp_path / "jira-session.jsonl"
    session.write_text(
        '{"type":"user","sessionId":"jira-e2e",'
        '"timestamp":"2026-09-19T01:00:00Z",'
        '"message":{"content":"ENG-184 is done."}}\n',
        encoding="utf-8",
    )
    IngestionService(
        ActivityRecordRepository(settings.activity_dir),
        data_directory=settings.data_dir,
        workspace_root=tmp_path,
        apply_git_overlay=False,
        jira_client=FakeJiraClient([issue]),
    ).ingest_file("claude", session)

    class JiraProvider(DemoProvider):
        async def generate(self, **kwargs):
            if kwargs["task"] != "draft":
                return await super().generate(**kwargs)
            evidence = json.loads(kwargs["user"])["evidence"]
            state = next(
                item for item in evidence if "jira.work_state [" in item["text"]
            )
            jira_issue = next(
                item for item in evidence if "jira.issue [" in item["text"]
            )
            state_result = state["text"].split("Recorded result: ", 1)[1]
            issue_result = jira_issue["text"].split("Recorded result: ", 1)[1]
            report = {
                key: {"text": UNKNOWN, "citations": []}
                for key in (
                    "starting_state",
                    "approach",
                    "changes",
                    "result",
                    "links",
                    "blockers",
                )
            }
            report["result"] = {
                "text": state_result,
                "citations": [
                    {
                        "evidence_id": state["evidence_id"],
                        "quote": state_result,
                    }
                ],
            }
            report["links"] = {
                "text": issue_result,
                "citations": [
                    {
                        "evidence_id": jira_issue["evidence_id"],
                        "quote": issue_result,
                    }
                ],
            }
            return report

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True, "ts": "123.456"})

    with TestClient(
        create_app(
            settings,
            provider=JiraProvider(),
            transport=httpx.MockTransport(handler),
        )
    ) as client:
        client.headers["Authorization"] = "Bearer " + KEY
        client.app.state.heartbeat.jira_client = FakeJiraClient([issue])
        assert client.post("/api/refresh").status_code == 200
        client.post("/api/personas", json=persona_payload())
        draft = new_draft(client, retrieval={"query": "ENG-184"})
        assert any(
            "jira.work_state" in item["text"]
            for item in draft["evidence"]
        )
        draft = approve(client, draft)
        delivered = client.post(
            f"/api/drafts/{draft['id']}/deliver",
            json={"expected_revision": draft["revision"]},
        ).json()
        assert delivered["status"] == "delivered"

    assert len(calls) == 1
    payload = json.loads(calls[0].content)
    assert "Jira still shows In Progress" in payload["text"]
    assert "https://example.atlassian.net/browse/ENG-184" in payload["text"]


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


def test_heartbeat_refreshes_jira_for_unchanged_activity(client, record, settings):
    record["prompts"] = ["Check ENG-184."]
    repository = ActivityRecordRepository(settings.activity_dir)
    repository.save(ActivityRecord.model_validate(record))
    heartbeat = client.app.state.heartbeat
    heartbeat.jira_client = FakeJiraClient(
        [
            JiraIssue(
                key="ENG-184",
                summary="Fix overlay",
                status="In Progress",
                status_category="indeterminate",
                url="https://example.atlassian.net/browse/ENG-184",
            )
        ]
    )
    assert client.post("/api/refresh").json()["changed"] == 1
    heartbeat.jira_client = FakeJiraClient(
        [
            JiraIssue(
                key="ENG-184",
                summary="Fix overlay",
                status="Done",
                status_category="done",
                url="https://example.atlassian.net/browse/ENG-184",
            )
        ]
    )
    assert client.post("/api/refresh").json()["changed"] == 1
    matches = client.post(
        "/api/retrieval/search",
        json={"query": "ENG-184"},
    ).json()["matches"]
    calls = matches[0]["record"]["tool_calls"]
    states = [call for call in calls if call["name"] == "jira.work_state"]
    assert len(states) == 1
    assert states[0]["result_summary"] == "ENG-184 is Done."


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


def test_recent_search_keeps_scope_and_prefers_new_commit(client, record):
    import copy

    from virtual_you.contracts.reporting import RetrievalRequest
    retrieval = client.app.state.retrieval
    for session, date, project in [('old', '2026-09-01', 'allowed'), ('new', '2026-09-02', 'allowed'), ('private', '2026-09-03', 'private')]:
        value = copy.deepcopy(record)
        value['session_id'] = session
        value['timestamp_range'] = {'started_at': date+'T00:00:00Z', 'ended_at': date+'T01:00:00Z'}
        value['end_state'] = 'Recorded commit: '+session
        retrieval.upsert(value)
        retrieval.assign_project([session], project)
    request = RetrievalRequest(query='commit', project_ids=['allowed'], sort='recent', limit=1)
    assert retrieval.search(request)[0]['record']['session_id'] == 'new'
    assert {e.session_id for e in retrieval.evidence(request)} == {'new'}


def test_jira_refresh_rejects_unredacted_feed_before_remote_lookup(settings, record):
    settings.activity_feed_url = "https://ingestion.example/activities"
    record["redacted"] = False
    record["prompts"] = ["Check ENG-184."]
    with TestClient(create_app(
        settings, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[record]))
    )) as client:
        client.headers["Authorization"] = "Bearer " + KEY
        jira = FakeJiraClient([])
        client.app.state.heartbeat.jira_client = jira
        status = client.post("/api/refresh").json()
        assert status["state"] == "degraded"
        assert status["count"] == 0
        assert jira.calls == []
