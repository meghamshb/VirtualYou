from datetime import datetime, timezone
from hashlib import sha256
from hmac import new as hmac_new
from pathlib import Path

import pytest

from virtual_you.contracts.activity import ActivityRecord
from virtual_you.ingest.service import IngestionService
from virtual_you.ingest.store import ActivityRecordRepository
from virtual_you.mcp.enrich import enrich, github_enabled
from virtual_you.mcp.followup import ESCALATE, answer
from virtual_you.mcp.github import CheckRun, FakeGitHubClient, GitHubSnapshot, PullRequest, RestGitHubClient, Review
from virtual_you.mcp.oauth import (
    OAuthError,
    authorize_url,
    complete_callback,
    exchange_code,
    load_token,
    new_state,
    save_token,
)
from virtual_you.mcp.observations import ObservationStore
from virtual_you.mcp.reconcile import reconcile
from virtual_you.mcp.webhooks import apply_webhook, observations_from_event, verify_signature
from virtual_you.mcp.work_state import NOT_READY_CI, Observation, reduce_sentence


SHA = "abcdef1234567890abcdef1234567890abcdef12"
OTHER = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
PR_URL = "https://github.com/org/repo/pull/12"
CI_URL = "https://github.com/org/repo/actions/runs/9"


def _record(**overrides) -> ActivityRecord:
    payload = {
        "schema_version": "1.0",
        "session_id": "session-1",
        "source": "claude",
        "start_state": "HEAD {} add overlay".format(SHA),
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


def _failed_ci_snapshot(sha: str = SHA) -> GitHubSnapshot:
    return GitHubSnapshot(
        sha=sha,
        commit_url="https://github.com/org/repo/commit/{}".format(sha),
        commit_time="2026-09-19T12:00:00Z",
        pull_requests=[
            PullRequest(
                number=12,
                url=PR_URL,
                state="open",
                title="Fix overlay",
                head_sha=sha,
            )
        ],
        checks=[
            CheckRun(
                name="tests",
                conclusion="failure",
                url=CI_URL,
                sha=sha,
                event_id="check:9",
            )
        ],
        reviews=[
            Review(
                review_id="88",
                state="CHANGES_REQUESTED",
                submitted_at="2026-09-19T12:10:00Z",
                sha=sha,
                url="https://github.com/org/repo/pull/12#discussion",
            )
        ],
    )


def test_github_flag_defaults_off(monkeypatch) -> None:
    monkeypatch.delenv("VIRTUAL_YOU_MCP_GITHUB", raising=False)
    assert github_enabled() is False


def test_flags_off_enrich_is_noop() -> None:
    record = _record()
    client = FakeGitHubClient([_failed_ci_snapshot()])
    out = enrich(record, client=client, enabled=False)
    assert out.tool_calls == []
    assert client.calls == []


def test_session_tests_passed_and_ci_failed_same_sha() -> None:
    record = _record(prompts=["Implement the change. tests passed"])
    out = enrich(
        record,
        client=FakeGitHubClient([_failed_ci_snapshot()]),
        enabled=True,
    )
    names = {call.name: call.result_summary for call in out.tool_calls}
    assert names["github.work_state"] == NOT_READY_CI
    assert "failure" in names["github.ci"]
    assert PR_URL in names["github.pr"]
    assert CI_URL in names["github.ci"]


def test_ci_on_other_sha_is_ignored() -> None:
    record = _record()
    out = enrich(
        record,
        client=FakeGitHubClient([_failed_ci_snapshot(OTHER)]),
        enabled=True,
    )
    names = {call.name: call.result_summary for call in out.tool_calls}
    assert names["github.work_state"] != NOT_READY_CI
    assert "github.ci" not in names


def test_dirty_tree_stops_at_edited() -> None:
    record = _record(end_state="app.py modified")
    sentence = reduce_sentence(record, [])
    assert sentence == "Edited, not committed."


def test_cursor_succeeded_is_claim_not_tests_passed() -> None:
    record = _record(
        tool_calls=[
            {
                "call_id": "1",
                "name": "StrReplace",
                "status": "succeeded",
                "input_summary": "",
                "result_summary": "",
            }
        ]
    )
    out = enrich(record, client=FakeGitHubClient([]), enabled=True)
    summary = next(
        call.result_summary
        for call in out.tool_calls
        if call.name == "github.work_state"
    )
    assert "tests passed" not in summary.lower()


def test_secret_in_github_payload_is_redacted(tmp_path: Path) -> None:
    secret = "sk-live-github-leak"
    snap = GitHubSnapshot(
        sha=SHA,
        commit_url="https://example.test/{}".format(secret),
        commit_time="2026-09-19T12:00:00Z",
    )
    out = enrich(
        _record(),
        client=FakeGitHubClient([snap]),
        extra_secrets=[secret],
        enabled=True,
    )
    dumped = out.model_dump_json()
    assert secret not in dumped
    assert "[REDACTED]" in dumped


def test_duplicate_webhook_delivery_writes_once(tmp_path: Path) -> None:
    store = ObservationStore(tmp_path)
    payload = {
        "check_run": {
            "id": 9,
            "head_sha": SHA,
            "conclusion": "failure",
            "html_url": CI_URL,
            "name": "tests",
            "completed_at": "2026-09-19T12:05:00Z",
        }
    }
    assert apply_webhook(store, event="check_run", delivery_id="del-1", payload=payload) == 1
    assert apply_webhook(store, event="check_run", delivery_id="del-1", payload=payload) == 0
    assert len(store.all()) == 1


def test_reconcile_recovers_dropped_check_run(tmp_path: Path) -> None:
    store = ObservationStore(tmp_path)
    store.append(
        Observation(
            source="github.commit",
            timestamp="2026-09-19T12:00:00Z",
            sha=SHA,
            task_id="session-1",
            kind="committed",
            status="verified",
            detail="sha {} committed".format(SHA),
            event_id="commit:{}".format(SHA),
        )
    )
    client = FakeGitHubClient([_failed_ci_snapshot()])
    written = reconcile(store, client, shas=[SHA])
    assert written >= 1
    kinds = {item.kind for item in store.all()}
    assert "checks" in kinds
    assert reconcile(store, client, shas=[SHA]) == 0


def test_followup_without_sha_escalates() -> None:
    record = _record(start_state="no git here", files_changed=[], end_state="")
    client = FakeGitHubClient([_failed_ci_snapshot()])
    result = answer("Did the fix pass CI?", record=record, client=client)
    assert result.escalated is True
    assert result.text == ESCALATE
    assert client.calls == []


def test_followup_blocking_and_ci() -> None:
    record = enrich(
        _record(),
        client=FakeGitHubClient([_failed_ci_snapshot()]),
        enabled=True,
    )
    blocking = answer(
        "What's blocking your PR?",
        record=record,
        client=FakeGitHubClient([_failed_ci_snapshot()]),
    )
    assert blocking.escalated is False
    assert "CHANGES_REQUESTED" in blocking.text
    ci = answer(
        "Did the fix pass CI?",
        record=record,
        client=FakeGitHubClient([_failed_ci_snapshot()]),
    )
    assert "fail" in ci.text.lower()


def test_followup_after_review_omits_earlier_commits() -> None:
    observations = [
        Observation(
            source="github.review",
            timestamp="2026-09-19T12:10:00Z",
            sha=SHA,
            task_id="12",
            kind="review",
            status="verified",
            detail="review",
            extra={"submitted_at": "2026-09-19T12:10:00Z"},
        ),
        Observation(
            source="github.commit",
            timestamp="2026-09-19T11:00:00Z",
            sha=SHA,
            task_id="12",
            kind="committed",
            status="verified",
            detail="old commit",
        ),
        Observation(
            source="github.commit",
            timestamp="2026-09-19T13:00:00Z",
            sha=SHA,
            task_id="12",
            kind="committed",
            status="verified",
            detail="new commit after review",
        ),
    ]
    result = answer(
        "What changed after my review?",
        record=_record(),
        observations=observations,
    )
    assert "new commit after review" in result.text
    assert "old commit" not in result.text


def test_ingest_with_injected_github_client(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VIRTUAL_YOU_MCP_GITHUB", "true")
    session = tmp_path / "session.jsonl"
    session.write_text(
        '{"type":"user","sessionId":"s1","timestamp":"2026-09-19T01:00:00Z",'
        '"message":{"content":"Implement the change. tests passed"}}\n',
        encoding="utf-8",
    )
    service = IngestionService(
        ActivityRecordRepository(tmp_path / "activities"),
        data_directory=tmp_path,
        now=lambda: datetime(2026, 9, 19, 3, tzinfo=timezone.utc),
        env_search_root=tmp_path,
        workspace_root=tmp_path,
        github_client=FakeGitHubClient([_failed_ci_snapshot()]),
    )
    record = service.ingest_file("claude", session)
    # no overlay SHA in a non-git workspace; client must not mix other SHAs
    assert all(call.name != "github.ci" for call in record.tool_calls)


def test_rest_client_maps_injected_http(monkeypatch) -> None:
    def http_get(url, headers, timeout):
        assert headers["Authorization"] == "Bearer tok"
        if url.endswith("/commits/" + SHA):
            return {
                "sha": SHA,
                "html_url": "https://github.com/org/repo/commit/" + SHA,
                "commit": {"committer": {"date": "2026-09-19T12:00:00Z"}},
            }
        if url.endswith("/pulls"):
            return [
                {
                    "number": 12,
                    "html_url": PR_URL,
                    "state": "open",
                    "title": "Fix overlay",
                    "head": {"sha": SHA},
                    "merged_at": None,
                }
            ]
        if "check-runs" in url:
            return {
                "check_runs": [
                    {
                        "id": 9,
                        "name": "tests",
                        "conclusion": "success",
                        "html_url": CI_URL,
                    }
                ]
            }
        if "/reviews" in url:
            return [
                {
                    "id": 3,
                    "state": "APPROVED",
                    "submitted_at": "2026-09-19T12:10:00Z",
                    "commit_id": SHA,
                    "html_url": "",
                }
            ]
        if "/issues/" in url:
            return {"html_url": PR_URL, "title": "Fix overlay", "state": "open", "labels": []}
        return None

    client = RestGitHubClient("org/repo", "tok", http_get=http_get)
    snap = client.snapshot_for_sha(SHA)
    assert snap is not None
    assert snap.sha == SHA
    assert snap.pull_requests[0].number == 12
    assert snap.checks[0].conclusion == "success"
    assert snap.reviews[0].state == "APPROVED"


def test_rest_client_from_env_requires_repo(monkeypatch) -> None:
    monkeypatch.delenv("VIRTUAL_YOU_GITHUB_REPO", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert RestGitHubClient.from_env({}) is None
    monkeypatch.setenv("VIRTUAL_YOU_GITHUB_REPO", "org/repo")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    client = RestGitHubClient.from_env(
        {"VIRTUAL_YOU_GITHUB_REPO": "org/repo", "GITHUB_TOKEN": "tok"}
    )
    assert client is not None
    assert client.repo == "org/repo"


def test_webhook_maps_pull_request_and_review() -> None:
    pr_obs = observations_from_event(
        "pull_request",
        "d2",
        {
            "action": "opened",
            "pull_request": {
                "number": 12,
                "html_url": PR_URL,
                "state": "open",
                "merged": False,
                "updated_at": "2026-09-19T12:00:00Z",
                "head": {"sha": SHA},
            },
        },
    )
    assert pr_obs[0].kind == "pr"
    review_obs = observations_from_event(
        "pull_request_review",
        "d3",
        {
            "review": {
                "id": 8,
                "state": "changes_requested",
                "commit_id": SHA,
                "submitted_at": "2026-09-19T12:10:00Z",
                "html_url": PR_URL,
            },
            "pull_request": {"number": 12, "head": {"sha": SHA}},
        },
    )
    assert "CHANGES_REQUESTED" in review_obs[0].detail
    push_obs = observations_from_event(
        "push",
        "d4",
        {"after": SHA, "head_commit": {"id": SHA, "url": "https://github.com/c", "timestamp": "t"}},
    )
    assert push_obs[0].kind == "committed"
    assert observations_from_event("fork", "d5", {}) == []


def test_webhook_signature_roundtrip() -> None:
    body = b'{"ok":true}'
    secret = "whsec"
    digest = hmac_new(secret.encode(), body, sha256).hexdigest()
    assert verify_signature(secret, body, "sha256=" + digest)
    assert not verify_signature(secret, body, "sha256=deadbeef")


def test_oauth_token_store_and_from_env(tmp_path: Path) -> None:
    save_token(tmp_path, {"access_token": "gho_stored", "login": "yash"})
    stored = tmp_path / "github-oauth.json"
    assert load_token(tmp_path) == "gho_stored"
    assert stored.stat().st_mode & 0o777 == 0o600
    client = RestGitHubClient.from_env(
        {"VIRTUAL_YOU_GITHUB_REPO": "org/repo"},
        data_directory=tmp_path,
    )
    assert client is not None
    assert client.token == "gho_stored"
    env_client = RestGitHubClient.from_env(
        {"VIRTUAL_YOU_GITHUB_REPO": "org/repo", "GITHUB_TOKEN": "envtok"},
        data_directory=tmp_path,
    )
    assert env_client is not None
    assert env_client.token == "envtok"


def test_oauth_authorize_url_and_callback(tmp_path: Path) -> None:
    env = {
        "GITHUB_CLIENT_ID": "iv1client",
        "GITHUB_CLIENT_SECRET": "supersecret",
        "GITHUB_OAUTH_REDIRECT": "http://127.0.0.1:8765/github/callback",
    }
    state = new_state(tmp_path)
    url = authorize_url(state, env)
    assert "client_id=iv1client" in url
    assert "github.com/login/oauth/authorize" in url
    with pytest.raises(OAuthError):
        complete_callback(tmp_path, "code", "wrong-state", env)
    state = new_state(tmp_path)

    def post(url, fields):
        assert fields["code"] == "abc"
        assert "supersecret" not in url
        return {"access_token": "gho_x", "token_type": "bearer", "scope": "public_repo"}

    def get(url, authorization):
        assert authorization == "Bearer gho_x"
        return {"login": "yash"}

    payload = exchange_code("abc", env, http_post=post, http_get=get)
    assert payload["login"] == "yash"
    save_token(tmp_path, payload)
    assert load_token(tmp_path) == "gho_x"


def test_enrich_records_manager_followups() -> None:
    out = enrich(
        _record(),
        client=FakeGitHubClient([_failed_ci_snapshot()]),
        enabled=True,
    )
    names = {call.name: call.result_summary for call in out.tool_calls}
    assert "fail" in names["github.ask.ci"].lower()
    assert CI_URL in names["github.ask.ci"] or CI_URL in names["github.ci"]
    assert "github.ask.blocking" in names
    assert "github.ask.after_review" in names

