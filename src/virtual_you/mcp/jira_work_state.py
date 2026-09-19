"""Deterministic Jira work-state reducer for one named ticket."""

from __future__ import annotations

from typing import TYPE_CHECKING

from virtual_you.contracts.activity import ActivityRecord
from virtual_you.mcp.extract import claimed_jira_blocked, claimed_jira_done

if TYPE_CHECKING:
    from virtual_you.mcp.jira import JiraIssue


def reduce_jira_sentence(record: ActivityRecord, issue: JiraIssue) -> str:
    """Reduce session claims and verified Jira fields into one fixed sentence."""

    key = issue.key
    status = issue.status or "UNKNOWN"
    category = (issue.status_category or "").strip().lower()
    done_claim = claimed_jira_done(record, key)
    blocked_claim = claimed_jira_blocked(record, key)
    blocked = _is_blocked(issue)

    if done_claim and category != "done":
        return (
            "The session says {} is done, but Jira still shows {}. "
            "I wouldn't call it closed yet."
        ).format(key, status)
    if category == "done":
        return "{} is Done.".format(key)
    if blocked:
        return "{} is blocked in Jira. Not ready.".format(key)
    if blocked_claim:
        return "Session claims {} is blocked; Jira still shows {}.".format(
            key,
            status,
        )
    if category == "new":
        return (
            "{} is still {} in Jira. I wouldn't call it in progress yet."
        ).format(key, status)
    if category == "indeterminate" or status.lower() == "in progress":
        return "{} is {}.".format(key, status)
    return "{} was mentioned; Jira status is {}.".format(key, status)


def _is_blocked(issue: JiraIssue) -> bool:
    status = (issue.status or "").strip().lower()
    return status in {"blocked", "impediment"}
