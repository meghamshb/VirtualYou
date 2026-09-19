"""Read-only Jira: one named issue GET. No board dump, no JQL, no write."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Protocol, Sequence
from urllib.parse import quote, urljoin, urlparse

from virtual_you.contracts.activity import ActivityRecord, ToolCall
from virtual_you.ingest.redact import assert_safe_serialized, redact_value
from virtual_you.mcp.extract import extract_jira_keys
from virtual_you.mcp.jira_work_state import reduce_jira_sentence

try:
    import httpx
except ImportError:
    httpx = None

JIRA_FLAG_ENV = "VIRTUAL_YOU_MCP_JIRA"
JIRA_BASE_ENV = "JIRA_BASE_URL"
JIRA_EMAIL_ENV = "JIRA_EMAIL"
JIRA_TOKEN_ENV = "JIRA_API_TOKEN"
TRUE_VALUES = {"1", "true", "yes", "on"}
DEFAULT_TIMEOUT = 10.0
ISSUE_FIELDS = "summary,status,issuetype,priority,updated"


@dataclass(frozen=True)
class JiraIssue:
    key: str
    summary: str
    status: str
    url: str
    issue_type: str = ""
    priority: str = ""
    updated: str = ""
    status_category: str = ""


class JiraClient(Protocol):
    def get_issue(self, key: str) -> Optional[JiraIssue]:
        """Return one issue or None on miss/error. Never lists a board."""


class FakeJiraClient:
    """In-memory client for tests. Does not perform HTTP."""

    def __init__(self, issues: Optional[Sequence[JiraIssue]] = None) -> None:
        self.issues = list(issues or [])
        self.calls: List[str] = []

    def get_issue(self, key: str) -> Optional[JiraIssue]:
        self.calls.append(key)
        needle = (key or "").upper()
        for item in self.issues:
            if item.key.upper() == needle:
                return item
        return None


class RestJiraClient:
    """Read-only Jira Cloud REST. GET one issue by key."""

    def __init__(
        self,
        base_url: str,
        email: str,
        token: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        http_get=None,
    ) -> None:
        if urlparse(base_url).scheme.lower() != "https":
            raise ValueError("JIRA_BASE_URL must use HTTPS")
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.token = token
        self.timeout = timeout
        self._http_get = http_get

    @classmethod
    def from_env(cls, environ) -> Optional["RestJiraClient"]:
        base = (environ.get(JIRA_BASE_ENV) or "").strip()
        email = (environ.get(JIRA_EMAIL_ENV) or "").strip()
        token = (environ.get(JIRA_TOKEN_ENV) or "").strip()
        if not base or not email or not token:
            return None
        try:
            return cls(base, email, token)
        except ValueError:
            return None

    def get_issue(self, key: str) -> Optional[JiraIssue]:
        if not key:
            return None
        payload = self._get(
            "/rest/api/3/issue/{}?fields={}".format(quote(key), ISSUE_FIELDS)
        )
        if not isinstance(payload, dict):
            return None
        fields = payload.get("fields") or {}
        if not isinstance(fields, dict):
            fields = {}
        status = fields.get("status") or {}
        issue_type = fields.get("issuetype") or {}
        priority = fields.get("priority") or {}
        status_category = (
            status.get("statusCategory") or {}
            if isinstance(status, dict)
            else {}
        )
        found_key = str(payload.get("key") or key)
        return JiraIssue(
            key=found_key,
            summary=str(fields.get("summary") or ""),
            status=str(status.get("name") or "") if isinstance(status, dict) else "",
            url="{}/browse/{}".format(self.base_url, found_key),
            issue_type=(
                str(issue_type.get("name") or "")
                if isinstance(issue_type, dict)
                else ""
            ),
            priority=(
                str(priority.get("name") or "")
                if isinstance(priority, dict)
                else ""
            ),
            updated=str(fields.get("updated") or ""),
            status_category=(
                str(status_category.get("key") or "")
                if isinstance(status_category, dict)
                else ""
            ),
        )

    def _get(self, path: str):
        getter = self._http_get
        if getter is None:
            getter = _httpx_get
        raw = "{}:{}".format(self.email, self.token).encode("utf-8")
        return getter(
            urljoin(self.base_url + "/", path.lstrip("/")),
            headers={
                "Accept": "application/json",
                "Authorization": "Basic {}".format(base64.b64encode(raw).decode("ascii")),
            },
            timeout=self.timeout,
        )


def jira_enabled(environ=None) -> bool:
    env = environ if environ is not None else os.environ
    return (env.get(JIRA_FLAG_ENV) or "").strip().lower() in TRUE_VALUES


def enrich_jira(
    record: ActivityRecord,
    *,
    client: Optional[JiraClient] = None,
    extra_secrets: Optional[Sequence[str]] = None,
    enabled: Optional[bool] = None,
    now: Optional[datetime] = None,
) -> ActivityRecord:
    """No-op when the flag is off or no ticket key is in prompts. Never raises."""

    if enabled is None:
        enabled = jira_enabled()
    if not enabled:
        return record
    secrets = extra_secrets if extra_secrets is not None else ()
    keys = extract_jira_keys(record)
    if not keys:
        return record
    if client is None:
        return record

    fetched_at = now or datetime.now(timezone.utc)
    calls = list(record.tool_calls)
    existing = {call.call_id for call in calls}
    for key in keys:
        issue_id = "jira.issue:{}".format(key)
        state_id = "jira.work_state:{}".format(key)
        calls = [
            call
            for call in calls
            if call.call_id not in {issue_id, state_id}
        ]
        existing.discard(issue_id)
        existing.discard(state_id)
        try:
            issue = client.get_issue(key)
        except Exception:
            issue = None
        if issue is None or issue.key.upper() != key.upper():
            calls.append(
                ToolCall(
                    call_id=state_id,
                    name="jira.work_state",
                    input_summary=key,
                    result_summary=(
                        "{} was mentioned; Jira status is UNKNOWN."
                    ).format(key),
                    status="unknown",
                    timestamp=fetched_at,
                )
            )
            existing.add(state_id)
            continue
        call_id = "jira.issue:{}".format(issue.key)
        summary = '{} {} "{}" {}'.format(
            issue.key,
            issue.status,
            issue.summary,
            issue.url,
        ).strip()
        if issue.updated:
            summary = "{} updated {}".format(summary, issue.updated)
        observed_at = _issue_timestamp(issue.updated) or fetched_at
        if call_id not in existing:
            calls.append(
                ToolCall(
                    call_id=call_id,
                    name="jira.issue",
                    input_summary=issue.key,
                    result_summary=summary[:1500],
                    status="succeeded",
                    timestamp=observed_at,
                )
            )
            existing.add(call_id)
        state_id = "jira.work_state:{}".format(issue.key)
        if state_id not in existing:
            calls.append(
                ToolCall(
                    call_id=state_id,
                    name="jira.work_state",
                    input_summary=issue.key,
                    result_summary=reduce_jira_sentence(record, issue),
                    status="succeeded",
                    timestamp=observed_at,
                )
            )
            existing.add(state_id)

    payload = record.model_dump()
    payload["tool_calls"] = [call.model_dump() for call in calls]
    redacted = redact_value(payload, extra_secrets=secrets)
    redacted["schema_version"] = "1.0"
    redacted["redacted"] = True
    enriched = ActivityRecord.model_validate(redacted)
    assert_safe_serialized(enriched, extra_secrets=secrets)
    return enriched


def _httpx_get(url: str, headers: dict, timeout: float):
    if httpx is None:
        return None
    try:
        response = httpx.get(url, headers=headers, timeout=timeout)
        if response.status_code in (401, 403, 404):
            return None
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError:
        return None


def _issue_timestamp(value: str) -> Optional[datetime]:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
