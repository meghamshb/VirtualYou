"""Targeted follow-ups on a known PR/SHA. Escalate when evidence is missing."""

from dataclasses import dataclass
from typing import Optional, Sequence

from virtual_you.contracts.activity import ActivityRecord
from virtual_you.mcp.extract import extract_pr_number, extract_sha, same_sha
from virtual_you.mcp.github import GitHubClient, GitHubSnapshot
from virtual_you.mcp.work_state import Observation, for_sha

ESCALATE = "I don't have a PR or commit SHA in evidence — I'll flag it for the intern to confirm."

TARGET_QUESTIONS = (
    "Did the fix pass CI?",
    "What's blocking your PR?",
    "What changed after my review?",
)


@dataclass(frozen=True)
class FollowUpAnswer:
    text: str
    escalated: bool
    kind: str


def answer(
    question: str,
    *,
    record: Optional[ActivityRecord] = None,
    observations: Sequence[Observation] = (),
    client: Optional[GitHubClient] = None,
    snapshot: Optional[GitHubSnapshot] = None,
) -> FollowUpAnswer:
    sha = extract_sha(record) if record is not None else ""
    if not sha:
        for item in observations:
            if item.sha:
                sha = item.sha
                break
    kind = classify(question)
    if not sha:
        return FollowUpAnswer(text=ESCALATE, escalated=True, kind=kind)
    live = snapshot
    if live is None and client is not None:
        live = client.snapshot_for_sha(sha)
    scoped = for_sha(observations, sha)
    if kind == "blocking":
        return _blocking(sha, live, scoped, extract_pr_number(record) if record else None)
    if kind == "ci":
        return _ci(sha, live, scoped)
    if kind == "after_review":
        return _after_review(sha, live, scoped)
    return FollowUpAnswer(text=ESCALATE, escalated=True, kind=kind)


def answer_targets(
    *,
    record: Optional[ActivityRecord] = None,
    observations: Sequence[Observation] = (),
    client: Optional[GitHubClient] = None,
    snapshot: Optional[GitHubSnapshot] = None,
) -> list:
    """Run the three manager follow-ups. Interns do not type these."""

    return [
        answer(
            question,
            record=record,
            observations=observations,
            client=client,
            snapshot=snapshot,
        )
        for question in TARGET_QUESTIONS
    ]


def classify(question: str) -> str:
    text = (question or "").lower()
    if "block" in text:
        return "blocking"
    if "ci" in text or "test" in text or "check" in text or "pass" in text:
        return "ci"
    if "after" in text and "review" in text:
        return "after_review"
    if "review" in text:
        return "after_review"
    return "unknown"


def _blocking(
    sha: str,
    snapshot: Optional[GitHubSnapshot],
    observations: Sequence[Observation],
    pr_number: Optional[int],
) -> FollowUpAnswer:
    parts = []
    reviews = list((snapshot.reviews if snapshot else []))
    checks = list((snapshot.checks if snapshot else []))
    for item in observations:
        if item.kind == "review" or item.source == "github.review":
            if "CHANGES_REQUESTED" in (item.detail or "") or item.extra.get("state") == "CHANGES_REQUESTED":
                parts.append(item.detail or "CHANGES_REQUESTED")
        if item.kind == "checks" or item.source == "github.checks":
            if "fail" in (item.detail or "").lower() or "fail" in str(item.extra.get("conclusion") or "").lower():
                parts.append(item.detail)
    for review in reviews:
        if review.state == "CHANGES_REQUESTED" and same_sha(review.sha or sha, sha):
            parts.append("review CHANGES_REQUESTED on {}".format(sha))
    for check in checks:
        if "fail" in (check.conclusion or "").lower() and same_sha(check.sha or sha, sha):
            parts.append("{} {}".format(check.conclusion, check.url).strip())
    if not parts:
        if pr_number is None and snapshot is None:
            return FollowUpAnswer(text=ESCALATE, escalated=True, kind="blocking")
        return FollowUpAnswer(
            text="No blocking review or failing check recorded for this SHA.",
            escalated=False,
            kind="blocking",
        )
    unique = []
    for part in parts:
        if part and part not in unique:
            unique.append(part)
    return FollowUpAnswer(text="\n".join(unique), escalated=False, kind="blocking")


def _ci(
    sha: str,
    snapshot: Optional[GitHubSnapshot],
    observations: Sequence[Observation],
) -> FollowUpAnswer:
    conclusion = None
    detail = ""
    if snapshot is not None:
        for check in snapshot.checks:
            if not same_sha(check.sha or sha, sha):
                continue
            conclusion = check.conclusion
            detail = "{} {}".format(check.conclusion, check.url).strip()
    for item in observations:
        if item.kind != "checks" and item.source != "github.checks":
            continue
        if item.sha and not same_sha(item.sha, sha):
            continue
        conclusion = str(item.extra.get("conclusion") or conclusion or "")
        detail = item.detail or detail
    if not conclusion and not detail:
        return FollowUpAnswer(
            text="No CI result recorded for this SHA.",
            escalated=False,
            kind="ci",
        )
    return FollowUpAnswer(text=detail or conclusion, escalated=False, kind="ci")


def _after_review(
    sha: str,
    snapshot: Optional[GitHubSnapshot],
    observations: Sequence[Observation],
) -> FollowUpAnswer:
    submitted = ""
    for item in observations:
        if item.kind == "review" or item.source == "github.review":
            submitted = str(item.extra.get("submitted_at") or item.timestamp or submitted)
    if snapshot is not None:
        for review in snapshot.reviews:
            if review.submitted_at > submitted:
                submitted = review.submitted_at
    if not submitted:
        return FollowUpAnswer(
            text="No review timestamp in evidence for this SHA.",
            escalated=False,
            kind="after_review",
        )
    later = []
    if snapshot is not None:
        for review in snapshot.reviews:
            if review.submitted_at and review.submitted_at > submitted:
                later.append("commit {} after review {}".format(review.sha, submitted))
    for item in observations:
        if item.kind == "committed" and item.timestamp and submitted:
            if item.timestamp > submitted:
                later.append(item.detail)
    if not later:
        return FollowUpAnswer(
            text="No commits recorded after the review at {}.".format(submitted),
            escalated=False,
            kind="after_review",
        )
    unique = []
    for part in later:
        if part not in unique:
            unique.append(part)
    return FollowUpAnswer(text="\n".join(unique), escalated=False, kind="after_review")
