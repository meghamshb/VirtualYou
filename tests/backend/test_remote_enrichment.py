import asyncio
import json
import subprocess

import pytest

from virtual_you.backend.collection import github_remote_repo
from virtual_you.backend.enrichment import MAX_LOOKUPS, RemoteEvidence
from virtual_you.backend.heartbeat import Heartbeat
from virtual_you.backend.retrieval import RetrievalService
from virtual_you.backend.store import Store
from virtual_you.contracts.reporting import RetrievalRequest
from virtual_you.mcp.github import FakeGitHubClient, GitHubSnapshot, PullRequest, RestGitHubClient
from virtual_you.mcp.jira import FakeJiraClient, JiraIssue


@pytest.fixture(autouse=True)
def isolate_remote_flags(monkeypatch):
    for key in ("VIRTUAL_YOU_MCP_GITHUB", "VIRTUAL_YOU_MCP_JIRA", "VIRTUAL_YOU_GITHUB_REPO"):
        monkeypatch.delenv(key, raising=False)


def git(directory, *args):
    return subprocess.run(
        ["git", "-C", str(directory), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


def setup_git(settings, tmp_path, monkeypatch):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    git(workspace, "init")
    git(workspace, "config", "user.name", "Test")
    git(workspace, "config", "user.email", "test@example.invalid")
    git(workspace, "remote", "add", "origin", "git@github.com:team/work.git")
    (workspace / "app.txt").write_text("Working change\n")
    git(workspace, "add", "app.txt")
    git(workspace, "commit", "-m", "SCRUM-1: implement the change")
    sha = git(workspace, "rev-parse", "HEAD")
    config = tmp_path / "sources.json"
    config.write_text(json.dumps([{
        "source": "git", "path": str(workspace), "workspace": str(workspace),
        "project": "allowed",
    }]))
    settings.ingestion_config = config
    settings.prepare()
    monkeypatch.setenv("VIRTUAL_YOU_GITHUB_REPO", "team/work")
    store = Store(settings.data_dir / "test.sqlite")
    retrieval = RetrievalService(store)
    return sha, store, retrieval


def snapshot(sha, merged=False):
    return GitHubSnapshot(
        sha, commit_url=f"https://github.com/team/work/commit/{sha}",
        pull_requests=[PullRequest(
            5, "https://github.com/team/work/pull/5", "closed" if merged else "open",
            "Implement the change", sha, merged=merged,
        )],
    )


def issue(status="In Progress"):
    return JiraIssue(
        "SCRUM-1", "Implement the change", status,
        "https://example.atlassian.net/browse/SCRUM-1", updated="2026-09-20T01:00:00Z",
        status_category="done" if status == "Done" else "indeterminate",
    )


def stored_calls(store):
    with store.connection() as db:
        payloads = [json.loads(row[0]) for row in db.execute("SELECT payload FROM activities")]
    return [call for payload in payloads for call in payload["tool_calls"]]


def test_git_refresh_replaces_remote_facts_without_new_commit(settings, tmp_path, monkeypatch):
    sha, store, retrieval = setup_git(settings, tmp_path, monkeypatch)
    github, jira = FakeGitHubClient([snapshot(sha)]), FakeJiraClient([issue()])
    # Legacy observations must never enter the project-scoped runtime index.
    (settings.data_dir / "github-observations.jsonl").write_text("private-unrelated-observation\n")
    heartbeat = Heartbeat(settings, store, retrieval, None, jira_client=jira, github_client=github)

    first = asyncio.run(heartbeat.refresh())
    assert first["state"] == "healthy", first
    assert first["enrichment"]["github"]["records_enriched"] == 1
    assert first["enrichment"]["jira"]["records_enriched"] == 1
    assert first["changed"] == 1
    calls = stored_calls(store)
    assert any(call["name"] == "github.pr" and "open #5" in call["result_summary"] for call in calls)
    assert any(call["name"] == "jira.issue" and "In Progress" in call["result_summary"] for call in calls)
    assert "private-unrelated-observation" not in json.dumps(calls)
    assert not retrieval.evidence(RetrievalRequest(project_ids=["denied"]))

    again = asyncio.run(heartbeat.refresh())
    assert again["changed"] == 0 and again["unchanged"] == 1
    assert again["collection"]["unchanged"] == 1
    assert github.calls == [sha] and jira.calls == ["SCRUM-1"]

    github.snapshots = [snapshot(sha, merged=True)]
    jira.issues = [issue("Done")]
    refreshed = asyncio.run(heartbeat.refresh(force_integrations=True))
    assert refreshed["collection"]["unchanged"] == 1
    assert refreshed["changed"] == 1
    calls = stored_calls(store)
    assert any(call["name"] == "github.pr" and "merged #5" in call["result_summary"] for call in calls)
    assert any(call["name"] == "jira.work_state" and call["result_summary"] == "SCRUM-1 is Done." for call in calls)
    assert not any("open #5" in call["result_summary"] for call in calls)
    assert all(call["timestamp"] for call in calls if call["name"].startswith("github."))

    # Failures remove the previous verified state; health reports safe actionable codes.
    github.snapshots, jira.issues = [], []
    failed = asyncio.run(heartbeat.refresh(force_integrations=True))
    assert failed["state"] == "degraded"
    assert {e["code"] for e in failed["errors"]} == {"github_lookup_failed", "jira_lookup_failed"}
    calls = stored_calls(store)
    assert not any(call["name"] in {"github.pr", "github.ci", "jira.issue"} for call in calls)
    assert all(call["status"] == "unknown" for call in calls if call["name"].startswith(("github.", "jira.")))
    assert failed["last_success_at"] == refreshed["last_success_at"]

    # Turning integrations off clears indexed remote facts even though HEAD/files did not change.
    heartbeat.github_client, heartbeat.jira_client = None, None
    disabled = asyncio.run(heartbeat.refresh())
    assert disabled["changed"] == 1
    assert not any(call["name"].startswith(("github.", "jira.")) for call in stored_calls(store))


@pytest.mark.parametrize("remote,expected", [
    ("git@github.com:team/work.git", "team/work"),
    ("https://github.com/team/work.git", "team/work"),
    ("ssh://git@github.com/team/work.git", "team/work"),
    ("https://github.com.evil.test/team/work.git", None),
    ("https://elsewhere.test/team/work.git", None),
    ("../other/repository", None),
])
def test_repository_scope_uses_exact_github_host(remote, expected):
    assert github_remote_repo(remote) == expected


def test_explicit_repo_and_origin_mismatch_do_not_mix_projects(settings, tmp_path, monkeypatch):
    sha, store, retrieval = setup_git(settings, tmp_path, monkeypatch)
    monkeypatch.setenv("VIRTUAL_YOU_GITHUB_REPO", "team/other")
    github = FakeGitHubClient([snapshot(sha)])
    heartbeat = Heartbeat(settings, store, retrieval, None, github_client=github)
    result = asyncio.run(heartbeat.refresh())
    assert github.calls == []
    assert not any(call["name"].startswith("github.") for call in stored_calls(store))
    assert result["errors"] == [{"source": "github", "code": "github_scope_unavailable"}]
    entries = json.loads(settings.ingestion_config.read_text())
    entries[0]["github_repo"] = "team/work"
    settings.ingestion_config.write_text(json.dumps(entries))
    assert asyncio.run(heartbeat.refresh())["enrichment"]["github"]["records_enriched"] == 1
    assert github.calls == [sha]
    entries[0].pop("github_repo")
    settings.ingestion_config.write_text(json.dumps(entries))
    asyncio.run(heartbeat.refresh())
    assert not any(call["name"].startswith("github.") for call in stored_calls(store))


def test_refresh_budget_deduplicates_and_never_reuses_expired_verified_facts(settings, record):
    settings.prepare()
    client = FakeGitHubClient([snapshot(f"{n:040x}") for n in range(1, MAX_LOOKUPS + 3)])
    remote = RemoteEvidence()
    cycle = remote.begin(settings, {"allowed": "team/work"}, github_client=client)
    results = []
    for n in range(1, MAX_LOOKUPS + 3):
        payload = {**record, "end_state": f"Recorded Git commit {n:040x}", "prompts": []}
        results.append(cycle.apply(payload, "allowed"))
    assert len(client.calls) == MAX_LOOKUPS
    assert cycle.summary()["github"]["deferred"] == 2
    assert any(call["status"] == "unknown" for call in results[-1]["tool_calls"])
    assert not any(call["name"] == "github.pr" for call in results[-1]["tool_calls"])
    cycle.apply({**record, "end_state": f"Recorded Git commit {1:040x}", "prompts": []}, "allowed")
    assert len(client.calls) == MAX_LOOKUPS
    # Expire the cache, then exhaust the time budget. Old remote facts are not replayed as current.
    for cached in remote.cache.values():
        cached.at = 0
    next_cycle = remote.begin(settings, {"allowed": "team/work"}, github_client=client)
    next_cycle.deadline = 0
    result = next_cycle.apply(results[0], "allowed")
    assert not any(call["name"] == "github.pr" for call in result["tool_calls"])
    assert next_cycle.stats["github"]["deferred"] == 1


def test_jira_only_named_references_shared_lookup_and_remote_secret_redaction(settings, record, monkeypatch):
    settings.prepare()
    monkeypatch.setenv("JIRA_API_TOKEN", "private-test-token-123")
    jira = FakeJiraClient([JiraIssue(
        "SCRUM-1", "Fix private-test-token-123", "Open", "https://jira.test/browse/SCRUM-1",
    )])
    cycle = RemoteEvidence().begin(settings, {}, jira_client=jira)
    unreferenced = {**record, "prompts": [], "diffs": ["+ SCRUM-1"], "end_state": "SCRUM-1"}
    assert not any(call["name"].startswith("jira.") for call in cycle.apply(unreferenced)["tool_calls"])
    assert jira.calls == []
    payload = {**record, "prompts": ["Investigate SCRUM-1"]}
    for project in ("allowed", "other"):
        enriched = cycle.apply(payload, project)
        assert "private-test-token-123" not in json.dumps(enriched)
        assert "[REDACTED]" in json.dumps(enriched)
    assert jira.calls == ["SCRUM-1"]


def test_partial_github_failure_is_not_evidence_of_no_blockers():
    def get(url, **kwargs):
        if url.endswith("/check-runs"):
            return None  # e.g. missing checks permission or transient failure
        if url.endswith("/pulls"):
            return []
        return {"sha": "a" * 40, "commit": {}}
    client = RestGitHubClient("team/work", "test-token", http_get=get)
    assert client.snapshot_for_sha("a" * 40) is None


def test_remote_requires_explicit_redacted_marker_before_lookup(settings, record):
    from virtual_you.backend.errors import ServiceError
    settings.prepare()
    github = FakeGitHubClient([snapshot("a" * 40)])
    cycle = RemoteEvidence().begin(settings, {"allowed": "team/work"}, github_client=github)
    payload = {**record, "end_state": "Recorded Git commit " + "a" * 40}
    payload.pop("redacted")
    with pytest.raises(ServiceError):
        cycle.apply(payload, "allowed")
    assert github.calls == []
