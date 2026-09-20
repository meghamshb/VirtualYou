"""Attach GitHub observations to an ActivityRecord as github.* tool_calls."""

import os
from datetime import datetime, timezone
from typing import List, Optional, Sequence
from uuid import uuid4

from virtual_you.contracts.activity import ActivityRecord, ToolCall
from virtual_you.ingest.redact import assert_safe_serialized, redact_value
from virtual_you.mcp.extract import extract_sha, has_edits, is_dirty, same_sha
from virtual_you.mcp.followup import answer_targets
from virtual_you.mcp.github import GitHubClient, GitHubSnapshot
from virtual_you.mcp.observations import ObservationStore
from virtual_you.mcp.work_state import Observation, reduce_sentence

GITHUB_FLAG_ENV = "VIRTUAL_YOU_MCP_GITHUB"
TRUE_VALUES = {"1", "true", "yes", "on"}


def github_enabled(environ=None) -> bool:
    env = environ if environ is not None else os.environ
    return (env.get(GITHUB_FLAG_ENV) or "").strip().lower() in TRUE_VALUES


def enrich(
    record: ActivityRecord,
    *,
    client: Optional[GitHubClient] = None,
    store: Optional[ObservationStore] = None,
    extra_secrets: Optional[Sequence[str]] = None,
    enabled: Optional[bool] = None,
    now: Optional[datetime] = None,
) -> ActivityRecord:
    """No-op when the flag is off. Never raises on GitHub failures."""

    if enabled is None:
        enabled = github_enabled()
    if not enabled:
        return record
    secrets = extra_secrets if extra_secrets is not None else ()

    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    sha = extract_sha(record)
    observations: List[Observation] = list(store.all() if store is not None else [])
    observations.extend(_session_observations(record, sha, timestamp))

    snapshot = None
    if client is not None and sha:
        try:
            snapshot = client.snapshot_for_sha(sha)
        except Exception:
            snapshot = None
    if snapshot is not None and not same_sha(snapshot.sha, sha):
        snapshot = None
    if snapshot is not None:
        observations.extend(_snapshot_observations(snapshot, record.session_id, timestamp))
        if store is not None:
            store.extend(_snapshot_observations(snapshot, record.session_id, timestamp))

    sentence = reduce_sentence(record, observations)
    calls = list(record.tool_calls)
    existing = {call.call_id for call in calls}
    for observation in observations:
        tool = _observation_tool_call(observation)
        if tool is None or tool.call_id in existing:
            continue
        calls.append(tool)
        existing.add(tool.call_id)
    state_id = "github.work_state:{}".format(sha or record.session_id)
    if state_id not in existing:
        calls.append(
            ToolCall(
                call_id=state_id,
                name="github.work_state",
                input_summary="work_state",
                result_summary=sentence,
                status="succeeded",
                timestamp=timestamp,
            )
        )
    for followup in answer_targets(
        record=record,
        observations=observations,
        snapshot=snapshot,
        # A failed snapshot must not cause three more network requests or claim recovery.
        client=None,
    ):
        ask_id = "github.ask.{}:{}".format(followup.kind, sha or record.session_id)
        if ask_id in existing:
            continue
        calls.append(
            ToolCall(
                call_id=ask_id,
                name="github.ask.{}".format(followup.kind),
                input_summary=followup.kind,
                result_summary=followup.text[:1500],
                status="succeeded" if not followup.escalated else "unknown",
                timestamp=timestamp,
            )
        )
        existing.add(ask_id)

    payload = record.model_dump()
    payload["tool_calls"] = [call.model_dump() for call in calls]
    redacted = redact_value(payload, extra_secrets=secrets)
    redacted["schema_version"] = "1.0"
    redacted["redacted"] = True
    enriched = ActivityRecord.model_validate(redacted)
    assert_safe_serialized(enriched, extra_secrets=secrets)
    return enriched


def _session_observations(
    record: ActivityRecord,
    sha: str,
    timestamp: str,
) -> List[Observation]:
    items: List[Observation] = []
    if record.prompts:
        items.append(
            Observation(
                source="session",
                timestamp=timestamp,
                sha=sha,
                task_id=record.session_id,
                kind="requested",
                status="claim",
                detail="user prompt recorded",
            )
        )
    if has_edits(record) or is_dirty(record):
        items.append(
            Observation(
                source="git_local" if sha else "session",
                timestamp=timestamp,
                sha=sha,
                task_id=record.session_id,
                kind="edited",
                status="observed" if sha else "claim",
                detail="local files changed",
            )
        )
    if sha and not is_dirty(record):
        items.append(
            Observation(
                source="git_local",
                timestamp=timestamp,
                sha=sha,
                task_id=record.session_id,
                kind="committed",
                status="observed",
                detail="sha {} committed".format(sha),
                event_id="git:{}".format(sha),
            )
        )
    return items


def _snapshot_observations(
    snapshot: GitHubSnapshot,
    task_id: str,
    timestamp: str,
) -> List[Observation]:
    items = [
        Observation(
            source="github.commit",
            timestamp=snapshot.commit_time or timestamp,
            sha=snapshot.sha,
            task_id=task_id,
            kind="committed",
            status="verified",
            detail="sha {} committed {}".format(snapshot.sha, snapshot.commit_time).strip(),
            url=snapshot.commit_url,
            event_id="commit:{}".format(snapshot.sha),
        )
    ]
    for check in snapshot.checks:
        items.append(
            Observation(
                source="github.checks",
                timestamp=timestamp,
                sha=check.sha or snapshot.sha,
                task_id=task_id,
                kind="checks",
                status="verified",
                detail="sha {} checks {} {}".format(
                    check.sha or snapshot.sha,
                    check.conclusion,
                    check.url,
                ).strip(),
                url=check.url,
                event_id=check.event_id or "check:{}:{}".format(snapshot.sha, check.name),
                extra={"conclusion": check.conclusion},
            )
        )
    for pull in snapshot.pull_requests:
        items.append(
            Observation(
                source="github.pr",
                timestamp=timestamp,
                sha=pull.head_sha or snapshot.sha,
                task_id=task_id,
                kind="merged" if pull.merged else "pr",
                status="verified",
                detail="{} {} #{} {}".format(
                    pull.url,
                    "merged" if pull.merged else pull.state,
                    pull.number,
                    pull.head_sha or snapshot.sha,
                ).strip(),
                url=pull.url,
                event_id="pr:{}".format(pull.number),
                extra={"merged": pull.merged, "number": pull.number},
            )
        )
    for review in snapshot.reviews:
        items.append(
            Observation(
                source="github.review",
                timestamp=review.submitted_at or timestamp,
                sha=review.sha or snapshot.sha,
                task_id=task_id,
                kind="review",
                status="verified",
                detail="sha {} review {} submitted {}".format(
                    review.sha or snapshot.sha,
                    review.state,
                    review.submitted_at,
                ).strip(),
                url=review.url,
                event_id="review:{}".format(review.review_id),
                extra={"state": review.state, "submitted_at": review.submitted_at},
            )
        )
    for issue in snapshot.issues:
        items.append(
            Observation(
                source="github.issue",
                timestamp=timestamp,
                sha=snapshot.sha,
                task_id=task_id,
                kind="issue",
                status="verified",
                detail="{} {} {}".format(issue.url, issue.state, issue.title).strip(),
                url=issue.url,
                event_id="issue:{}".format(issue.number),
            )
        )
    return items


def _observation_tool_call(observation: Observation) -> Optional[ToolCall]:
    name = {
        "committed": "github.commit",
        "checks": "github.ci",
        "pr": "github.pr",
        "merged": "github.pr",
        "review": "github.review",
        "issue": "github.issue",
    }.get(observation.kind)
    if name is None and observation.source.startswith("github."):
        name = observation.source
    if name is None:
        return None
    summary = observation.detail
    if observation.url and observation.url not in summary:
        summary = "{} {}".format(summary, observation.url).strip()
    return ToolCall(
        call_id=observation.event_id or "github:{}".format(uuid4().hex[:12]),
        name=name,
        input_summary=observation.source,
        result_summary=summary[:1500],
        status="succeeded" if observation.status == "verified" else "unknown",
        timestamp=observation.timestamp or None,
    )
