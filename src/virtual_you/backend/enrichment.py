"""Bounded read-only refresh of remote evidence inside each activity's project scope.

This path deliberately never reads/writes the legacy global ObservationStore.
Snapshots are cached briefly in memory; changing remote state replaces old facts.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from virtual_you.backend.errors import ServiceError
from virtual_you.contracts.activity import ActivityRecord, ToolCall
from virtual_you.ingest.redact import redact_value
from virtual_you.mcp.enrich import enrich, github_enabled
from virtual_you.mcp.extract import extract_jira_keys, extract_sha, same_sha
from virtual_you.mcp.github import FakeGitHubClient, RestGitHubClient
from virtual_you.mcp.jira import FakeJiraClient, RestJiraClient, enrich_jira, jira_enabled

TTL_SECONDS = 120
MAX_LOOKUPS = 10  # per integration, newest activities first
MAX_CACHE_ENTRIES = 200
MAX_REFRESH_SECONDS = 20


@dataclass
class Cached:
    at: float
    observed_at: datetime
    value: object


class RemoteEvidence:
    def __init__(self):
        self.cache = {}

    def begin(self, settings, projects, *, github_client=None, jira_client=None, force=False):
        return Refresh(
            self.cache, settings, projects,
            github_client=github_client, jira_client=jira_client, force=force,
        )


class Refresh:
    def __init__(self, cache, settings, projects, *, github_client, jira_client, force):
        self.cache, self.settings, self.projects, self.force = cache, settings, projects, force
        self.github_client, self.jira_client = github_client, jira_client
        self.github_on = github_client is not None or github_enabled()
        self.jira_on = jira_client is not None or jira_enabled()
        self.clients = {}
        self.seen = {}
        self.errors = []
        self.deadline = time.monotonic() + MAX_REFRESH_SECONDS
        self.stats = {
            name: {
                "enabled": enabled, "lookups": 0, "cached": 0, "failed": 0,
                "deferred": 0, "records_enriched": 0,
            }
            for name, enabled in (("github", self.github_on), ("jira", self.jira_on))
        }
        self.secrets = [os.getenv(k, "") for k in ("GITHUB_TOKEN", "JIRA_API_TOKEN")]
        if self.jira_on and self.jira_client is None:
            self.jira_client = RestJiraClient.from_env(os.environ)
        if self.jira_on and self.jira_client is None:
            self.error("jira", "jira_not_configured")
        if self.github_on and not projects:
            self.error("github", "github_scope_unavailable")

    def error(self, source, code):
        error = {"source": source, "code": code}
        if error not in self.errors:
            self.errors.append(error)

    def lookup(self, provider, key, fetch):
        cache_key = (provider, *key)
        if cache_key in self.seen:
            return self.seen[cache_key]
        stats = self.stats[provider]
        cached = self.cache.get(cache_key)
        if cached and not self.force and time.monotonic() - cached.at < TTL_SECONDS:
            stats["cached"] += 1
        elif stats["lookups"] >= MAX_LOOKUPS or time.monotonic() >= self.deadline:
            stats["deferred"] += 1
            self.seen[cache_key] = None
            return None
        else:
            stats["lookups"] += 1
            try:
                value = fetch()
            except Exception:
                value = None
            cached = Cached(time.monotonic(), datetime.now(timezone.utc), value)
            self.cache[cache_key] = cached
            while len(self.cache) > MAX_CACHE_ENTRIES:
                self.cache.pop(next(iter(self.cache)))
        if cached.value is None:
            stats["failed"] += 1
            self.error(provider, f"{provider}_lookup_failed")
        self.seen[cache_key] = cached
        return cached

    def apply(self, payload, project=None):
        if not isinstance(payload, dict) or payload.get("redacted") is not True:
            raise ServiceError(
                "unredacted_record", "Only explicitly redacted ActivityRecords are accepted.", 422
            )
        record = ActivityRecord.model_validate(redact_value(payload, extra_secrets=self.secrets))
        if self.github_on:
            record = self.github(record, project)
        else:
            record = without_calls(record, "github.")
        if self.jira_on:
            record = self.jira(record)
        else:
            record = without_calls(record, "jira.")
        return record.model_dump(mode="json")

    def github(self, record, project):
        repo = self.projects.get(project)
        # Remote calls from old/global ingestion must not survive outside approved scope.
        record = without_calls(record, "github.")
        if not repo or not (sha := extract_sha(record)):
            return record
        if repo not in self.clients:
            self.clients[repo] = self.github_client or RestGitHubClient.from_env(
                {**os.environ, "VIRTUAL_YOU_GITHUB_REPO": repo}, self.settings.data_dir,
            )
        client = self.clients[repo]
        if client is None:
            self.error("github", "github_not_configured")
            cached = None
        else:
            if isinstance(client, RestGitHubClient):
                client.timeout = 3
            token = getattr(client, "token", "")
            if token:
                self.secrets.append(token)
            # An injected client replacement invalidates its prior test snapshot.
            identity = id(client) if self.github_client is not None else "configured"
            cached = self.lookup("github", (repo, sha, identity), lambda: client.snapshot_for_sha(sha))
        snapshot = cached.value if cached else None
        if snapshot is not None and not same_sha(snapshot.sha, sha):
            snapshot = None
            self.error("github", "github_lookup_failed")
        if snapshot is None:
            return with_unknown(record, "github", sha, "GitHub state is UNKNOWN; refresh unavailable.")
        try:
            result = enrich(
                record, client=FakeGitHubClient([snapshot]), enabled=True,
                extra_secrets=self.secrets, now=cached.observed_at,
            )
        except Exception:
            self.error("github", "github_lookup_failed")
            return with_unknown(record, "github", sha, "GitHub state is UNKNOWN; refresh unavailable.")
        self.stats["github"]["records_enriched"] += 1
        return result

    def jira(self, record):
        keys = extract_jira_keys(record)
        record = without_calls(record, "jira.")
        if not keys:
            return record
        issues, observed = [], []
        for key in keys:
            if self.jira_client is None:
                continue
            client = self.jira_client
            if isinstance(client, RestJiraClient):
                client.timeout = 3
            identity = (getattr(client, "base_url", ""), id(client)) if not isinstance(
                client, RestJiraClient
            ) else (client.base_url, client.email)
            cached = self.lookup("jira", (*identity, key), lambda: client.get_issue(key))
            if cached:
                observed.append(cached.observed_at)
            if cached and cached.value and cached.value.key.upper() == key:
                issues.append(cached.value)
            elif cached and cached.value:
                self.error("jira", "jira_lookup_failed")
        # A fake here only replays the just-fetched/cached facts through shared redaction.
        # Missing/failed/deferred keys become UNKNOWN, replacing any formerly verified facts.
        result = enrich_jira(
            record, client=FakeJiraClient(issues), enabled=True, extra_secrets=self.secrets,
            now=max(observed) if observed else record.timestamp_range.ended_at,
        )
        if issues:
            self.stats["jira"]["records_enriched"] += 1
        return result

    def summary(self):
        return {
            "ttl_seconds": TTL_SECONDS, "lookup_limit": MAX_LOOKUPS,
            "time_budget_seconds": MAX_REFRESH_SECONDS, **self.stats,
        }


def without_calls(record, prefix):
    return record.model_copy(update={
        "tool_calls": [call for call in record.tool_calls if not call.name.startswith(prefix)],
    })


def with_unknown(record, provider, key, text):
    return record.model_copy(update={
        "tool_calls": [*record.tool_calls, ToolCall(
            call_id=f"{provider}.work_state:{key}", name=f"{provider}.work_state",
            input_summary=key, result_summary=text, status="unknown",
        )],
    })
