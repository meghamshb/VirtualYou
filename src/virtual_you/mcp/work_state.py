"""Deterministic requested→deployed reducer. The LLM does not pick the step."""

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from virtual_you.contracts.activity import ActivityRecord
from virtual_you.mcp.extract import (
    claimed_tests_passed,
    extract_sha,
    has_edits,
    is_dirty,
    same_sha,
    session_success_is_claim,
)

NOT_READY_CI = (
    "The implementation is committed, but the latest CI run failed. "
    "I wouldn't call it ready yet."
)
EDITED_NOT_COMMITTED = "Edited, not committed."
COMMITTED_TESTS_UNVERIFIED = "Committed; tests not verified."
COMMITTED_TESTS_PASSED_OPEN = (
    "Committed; tests passed on this SHA; not merged."
)
MERGED_DEPLOY_UNKNOWN = "Merged. Deployed stays UNKNOWN unless a deploy status exists."
BLOCKED_REVIEW_OR_CI = "Blocked: cite review and/or CI. Not ready."


@dataclass(frozen=True)
class Observation:
    source: str
    timestamp: str
    sha: str
    task_id: str
    kind: str
    status: str
    detail: str
    url: str = ""
    event_id: str = ""
    extra: dict = field(default_factory=dict)


def for_sha(observations: Sequence[Observation], sha: str) -> List[Observation]:
    if not sha:
        return [item for item in observations if not item.sha]
    return [
        item
        for item in observations
        if not item.sha or same_sha(item.sha, sha)
    ]


def reduce_sentence(
    record: ActivityRecord,
    observations: Sequence[Observation],
) -> str:
    sha = extract_sha(record)
    scoped = for_sha(observations, sha)
    dirty = is_dirty(record) or (has_edits(record) and not _committed(scoped, sha))
    committed = _committed(scoped, sha) and not dirty
    checks = _check_conclusion(scoped, sha)
    merged = any(
        item.kind == "merged" or (item.kind == "pr" and item.extra.get("merged"))
        for item in scoped
    )
    blocked = _blocked(scoped)
    claimed = claimed_tests_passed(record) or session_success_is_claim(record)

    if dirty or (has_edits(record) and not committed and not sha):
        return EDITED_NOT_COMMITTED
    if not committed:
        if has_edits(record):
            return EDITED_NOT_COMMITTED
        return "Requested." if record.prompts else "Not recorded in the selected activity."
    if checks == "failure":
        return NOT_READY_CI
    if blocked:
        return BLOCKED_REVIEW_OR_CI
    if merged:
        deployed = any(item.kind == "deployed" for item in scoped)
        if deployed:
            return "Deployed."
        return MERGED_DEPLOY_UNKNOWN
    if checks == "success":
        return COMMITTED_TESTS_PASSED_OPEN
    if claimed and checks is None:
        return COMMITTED_TESTS_UNVERIFIED
    return COMMITTED_TESTS_UNVERIFIED


def _committed(observations: Sequence[Observation], sha: str) -> bool:
    if not sha:
        return False
    return any(
        item.kind == "committed" or item.source in ("github.commit", "git_local")
        for item in observations
    )


def _check_conclusion(observations: Sequence[Observation], sha: str) -> Optional[str]:
    if not sha:
        return None
    latest = None
    for item in observations:
        if item.kind != "checks" and item.source != "github.checks":
            continue
        if item.sha and not same_sha(item.sha, sha):
            continue
        latest = item
    if latest is None:
        return None
    detail = (latest.detail or "").lower()
    extra_state = str(latest.extra.get("conclusion") or latest.extra.get("state") or "")
    token = extra_state.lower() or detail
    if "fail" in token:
        return "failure"
    if "success" in token or "pass" in token:
        return "success"
    return None


def _blocked(observations: Sequence[Observation]) -> bool:
    for item in observations:
        if item.kind == "review" or item.source == "github.review":
            if "CHANGES_REQUESTED" in (item.detail or "") or item.extra.get(
                "state"
            ) == "CHANGES_REQUESTED":
                return True
    return False
