from pathlib import Path

from virtual_you.contracts.activity import ActivityRecord
from virtual_you.ingest.service import IngestionService
from virtual_you.ingest.store import ActivityRecordRepository
from virtual_you.mcp.extract import extract_jira_keys
from virtual_you.mcp.jira import (
    FakeJiraClient,
    JiraIssue,
    RestJiraClient,
    enrich_jira,
    jira_enabled,
)

ISSUE_URL = "https://example.atlassian.net/browse/ENG-184"


def _record(**overrides) -> ActivityRecord:
    payload = {
        "schema_version": "1.0",
        "session_id": "session-1",
        "source": "claude",
        "start_state": "Working on the ticket.",
        "prompts": ["Implement the change."],
        "reasoning_summary": "",
        "files_changed": [{"path": "app.py", "operation": "modified"}],
        "diffs": [],
        "tool_calls": [],
        "end_state": "Callback validation is complete.",
        "timestamp_range": {
            "started_at": "2026-09-19T01:00:00Z",
            "ended_at": "2026-09-19T02:00:00Z",
        },
        "redacted": True,
    }
    payload.update(overrides)
    return ActivityRecord.model_validate(payload)


def _issue(**overrides) -> JiraIssue:
    payload = dict(
        key="ENG-184",
        summary="Fix overlay",
        status="In Progress",
        url=ISSUE_URL,
        issue_type="Task",
        priority="Medium",
        updated="2026-09-19T12:00:00.000+0000",
        status_category="indeterminate",
    )
    payload.update(overrides)
    return JiraIssue(**payload)


def test_jira_flag_defaults_off(monkeypatch) -> None:
    monkeypatch.delenv("VIRTUAL_YOU_MCP_JIRA", raising=False)
    assert jira_enabled() is False


def test_no_key_in_prompts_never_calls_client() -> None:
    client = FakeJiraClient([_issue()])
    record = _record()
    assert extract_jira_keys(record) == ()
    out = enrich_jira(record, client=client, enabled=True)
    assert client.calls == []
    assert out.tool_calls == record.tool_calls


def test_fake_issue_becomes_jira_tool_call() -> None:
    record = _record(prompts=["Please finish ENG-184 today."])
    out = enrich_jira(record, client=FakeJiraClient([_issue()]), enabled=True)
    names = {call.name: call.result_summary for call in out.tool_calls}
    assert "jira.issue" in names
    assert "ENG-184" in names["jira.issue"]
    assert "In Progress" in names["jira.issue"]
    assert ISSUE_URL in names["jira.issue"]
    assert "updated 2026-09-19T12:00:00.000+0000" in names["jira.issue"]
    assert names["jira.work_state"] == "ENG-184 is In Progress."
    ActivityRecord.model_validate(out.model_dump())


def test_secret_in_summary_is_redacted() -> None:
    secret = "sk-live-jira-leak"
    record = _record(prompts=["Work ENG-184"])
    out = enrich_jira(
        record,
        client=FakeJiraClient([_issue(summary="token " + secret, url=ISSUE_URL)]),
        extra_secrets=[secret],
        enabled=True,
    )
    dumped = out.model_dump_json()
    assert secret not in dumped
    assert "[REDACTED]" in dumped


def test_flag_off_is_identical() -> None:
    record = _record(prompts=["ENG-184 please"])
    client = FakeJiraClient([_issue()])
    out = enrich_jira(record, client=client, enabled=False)
    assert out.tool_calls == record.tool_calls
    assert client.calls == []


def test_http_403_skips_issue_and_keeps_session() -> None:
    def http_get(url, headers, timeout):
        return None

    client = RestJiraClient(
        "https://example.atlassian.net",
        "dev@example.com",
        "tok",
        http_get=http_get,
    )
    record = _record(prompts=["Look at ENG-184"])
    out = enrich_jira(record, client=client, enabled=True)
    assert all(call.name != "jira.issue" for call in out.tool_calls)
    state = next(
        call for call in out.tool_calls if call.name == "jira.work_state"
    )
    assert state.status == "unknown"
    assert state.result_summary == (
        "ENG-184 was mentioned; Jira status is UNKNOWN."
    )
    assert out.session_id == record.session_id


def test_rest_client_maps_injected_http() -> None:
    def http_get(url, headers, timeout):
        assert "Basic " in headers["Authorization"]
        assert url.endswith(
            "/rest/api/3/issue/ENG-184"
            "?fields=summary,status,issuetype,priority,updated"
        )
        return {
            "key": "ENG-184",
            "fields": {
                "summary": "Fix overlay",
                "status": {
                    "name": "In Progress",
                    "statusCategory": {"key": "indeterminate"},
                },
                "issuetype": {"name": "Task"},
                "priority": {"name": "Medium"},
                "updated": "2026-09-19T12:00:00.000+0000",
                "assignee": {
                    "displayName": "Avery Intern",
                    "emailAddress": "never-store@example.com",
                },
            },
        }

    client = RestJiraClient(
        "https://example.atlassian.net",
        "dev@example.com",
        "tok",
        http_get=http_get,
    )
    issue = client.get_issue("ENG-184")
    assert issue is not None
    assert issue.key == "ENG-184"
    assert issue.status == "In Progress"
    assert issue.status_category == "indeterminate"
    assert "never-store@example.com" not in str(issue)
    assert issue.url == ISSUE_URL


def test_rest_from_env_requires_all_fields() -> None:
    assert RestJiraClient.from_env({}) is None
    client = RestJiraClient.from_env(
        {
            "JIRA_BASE_URL": "https://example.atlassian.net",
            "JIRA_EMAIL": "dev@example.com",
            "JIRA_API_TOKEN": "tok",
        }
    )
    assert client is not None
    assert client.base_url == "https://example.atlassian.net"
    assert (
        RestJiraClient.from_env(
            {
                "JIRA_BASE_URL": "http://example.atlassian.net",
                "JIRA_EMAIL": "dev@example.com",
                "JIRA_API_TOKEN": "tok",
            }
        )
        is None
    )


def test_ingest_with_injected_jira_client(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VIRTUAL_YOU_MCP_JIRA", "true")
    session = tmp_path / "session.jsonl"
    session.write_text(
        '{"type":"user","sessionId":"s1","timestamp":"2026-09-19T01:00:00Z",'
        '"message":{"content":"Please finish ENG-184."}}\n',
        encoding="utf-8",
    )
    service = IngestionService(
        ActivityRecordRepository(tmp_path / "activities"),
        data_directory=tmp_path,
        env_search_root=tmp_path,
        workspace_root=tmp_path,
        apply_git_overlay=False,
        jira_client=FakeJiraClient([_issue()]),
    )
    record = service.ingest_file("claude", session)
    names = {call.name: call.result_summary for call in record.tool_calls}
    assert ISSUE_URL in names["jira.issue"]


def test_extract_caps_three_keys() -> None:
    record = _record(
        prompts=["AA-1 BB-2 CC-3 DD-4 should stop"],
    )
    assert extract_jira_keys(record) == ("AA-1", "BB-2", "CC-3")


def test_done_claim_conflicts_with_in_progress_jira() -> None:
    record = _record(
        prompts=["Finish ENG-184."],
        end_state="ENG-184 is done and ready to close.",
    )
    out = enrich_jira(
        record,
        client=FakeJiraClient([_issue()]),
        enabled=True,
    )
    states = {
        call.input_summary: call.result_summary
        for call in out.tool_calls
        if call.name == "jira.work_state"
    }
    assert states["ENG-184"] == (
        "The session says ENG-184 is done, but Jira still shows In Progress. "
        "I wouldn't call it closed yet."
    )


def test_blocked_jira_is_not_ready() -> None:
    record = _record(prompts=["Work on ENG-184."])
    out = enrich_jira(
        record,
        client=FakeJiraClient(
            [_issue(status="Impediment", priority="Blocker")]
        ),
        enabled=True,
    )
    state = next(
        call.result_summary
        for call in out.tool_calls
        if call.name == "jira.work_state"
    )
    assert state == "ENG-184 is blocked in Jira. Not ready."


def test_session_blocked_claim_does_not_override_jira() -> None:
    record = _record(
        prompts=["ENG-184 is blocked waiting on access."],
    )
    out = enrich_jira(
        record,
        client=FakeJiraClient([_issue()]),
        enabled=True,
    )
    state = next(
        call.result_summary
        for call in out.tool_calls
        if call.name == "jira.work_state"
    )
    assert state == (
        "Session claims ENG-184 is blocked; Jira still shows In Progress."
    )


def test_jira_done_is_verified() -> None:
    record = _record(prompts=["Check ENG-184."])
    out = enrich_jira(
        record,
        client=FakeJiraClient(
            [_issue(status="Resolved", status_category="done")]
        ),
        enabled=True,
    )
    state = next(
        call.result_summary
        for call in out.tool_calls
        if call.name == "jira.work_state"
    )
    assert state == "ENG-184 is Done."


def test_mismatched_issue_key_is_ignored() -> None:
    class MismatchedClient:
        def get_issue(self, key):
            return _issue(
                key="ENG-200",
                status="Done",
                status_category="done",
                url="https://example.atlassian.net/browse/ENG-200",
            )

    record = _record(prompts=["Check ENG-184."])
    out = enrich_jira(record, client=MismatchedClient(), enabled=True)
    assert all(call.name != "jira.issue" for call in out.tool_calls)
    state = next(
        call for call in out.tool_calls if call.name == "jira.work_state"
    )
    assert state.status == "unknown"


def test_refresh_replaces_stale_calls_for_same_key() -> None:
    record = _record(prompts=["Check ENG-184."])
    first = enrich_jira(
        record,
        client=FakeJiraClient([_issue()]),
        enabled=True,
    )
    refreshed = enrich_jira(
        first,
        client=FakeJiraClient(
            [_issue(status="Done", status_category="done")]
        ),
        enabled=True,
    )
    issue_calls = [
        call for call in refreshed.tool_calls if call.name == "jira.issue"
    ]
    state_calls = [
        call for call in refreshed.tool_calls if call.name == "jira.work_state"
    ]
    assert len(issue_calls) == len(state_calls) == 1
    assert state_calls[0].result_summary == "ENG-184 is Done."


def test_new_ticket_is_not_in_progress() -> None:
    record = _record(prompts=["Start ENG-184."])
    out = enrich_jira(
        record,
        client=FakeJiraClient(
            [_issue(status="To Do", status_category="new")]
        ),
        enabled=True,
    )
    state = next(
        call.result_summary
        for call in out.tool_calls
        if call.name == "jira.work_state"
    )
    assert state == (
        "ENG-184 is still To Do in Jira. "
        "I wouldn't call it in progress yet."
    )


def test_claims_attach_to_nearest_ticket_key() -> None:
    record = _record(
        prompts=["ENG-184 is done; ENG-200 is blocked waiting on access."],
    )
    out = enrich_jira(
        record,
        client=FakeJiraClient(
            [
                _issue(),
                _issue(
                    key="ENG-200",
                    status="In Progress",
                    url="https://example.atlassian.net/browse/ENG-200",
                ),
            ]
        ),
        enabled=True,
    )
    states = {
        call.input_summary: call.result_summary
        for call in out.tool_calls
        if call.name == "jira.work_state"
    }
    assert "session says ENG-184 is done" in states["ENG-184"]
    assert states["ENG-200"] == (
        "Session claims ENG-200 is blocked; Jira still shows In Progress."
    )


def test_ready_for_review_is_not_a_done_claim() -> None:
    record = _record(prompts=["ENG-184 is ready for review."])
    out = enrich_jira(
        record,
        client=FakeJiraClient([_issue()]),
        enabled=True,
    )
    state = next(
        call.result_summary
        for call in out.tool_calls
        if call.name == "jira.work_state"
    )
    assert state == "ENG-184 is In Progress."


def test_blocker_priority_does_not_mean_workflow_blocked() -> None:
    record = _record(prompts=["Work on ENG-184."])
    out = enrich_jira(
        record,
        client=FakeJiraClient([_issue(priority="Blocker")]),
        enabled=True,
    )
    state = next(
        call.result_summary
        for call in out.tool_calls
        if call.name == "jira.work_state"
    )
    assert state == "ENG-184 is In Progress."
