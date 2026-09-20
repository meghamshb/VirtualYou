"""Parse SHAs and session claims from an already-redacted ActivityRecord."""

import re
from typing import Optional, Sequence

from virtual_you.contracts.activity import ActivityRecord

HEAD_SHA_RE = re.compile(r"(?im)^HEAD\s+([0-9a-f]{7,40})\b")
SHA_RE = re.compile(r"\b([0-9a-f]{7,40})\b")
TESTS_PASSED_RE = re.compile(
    r"(?i)\b(tests?\s+passed|all tests passed|ci (is )?green|checks? passed)\b"
)
PR_RE = re.compile(r"(?i)\b(?:pull request|pr)\s*#?(\d+)\b")
JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
JIRA_DONE_RE = re.compile(r"(?i)\b(done|finished|completed?|closed)\b")
JIRA_BLOCKED_RE = re.compile(r"(?i)\b(blocked|blocking|impediment|waiting)\b")
JIRA_NEGATED_DONE_RE = re.compile(
    r"(?i)\b(?:not|isn't|isnt|wasn't|wasnt)\s+"
    r"(?:done|finished|completed?|closed|ready|shipped)\b"
)
JIRA_NEGATED_BLOCKED_RE = re.compile(
    r"(?i)\b(?:not|isn't|isnt|wasn't|wasnt)\s+"
    r"(?:blocked|blocking|an?\s+impediment|waiting)\b"
)
MAX_JIRA_KEYS = 3


def same_sha(left: str, right: str) -> bool:
    if not left or not right:
        return False
    shorter, longer = sorted((left.lower(), right.lower()), key=len)
    return longer.startswith(shorter) and len(shorter) >= 7


def extract_sha(record: ActivityRecord) -> str:
    match = HEAD_SHA_RE.search(record.start_state or "")
    if match:
        return match.group(1)
    for blob in (record.end_state, "\n".join(record.prompts)):
        found = SHA_RE.search(blob or "")
        if found and len(found.group(1)) >= 7:
            return found.group(1)
    for call in record.tool_calls:
        if call.name.startswith("github."):
            found = SHA_RE.search(call.result_summary or "")
            if found:
                return found.group(1)
    return ""


def extract_pr_number(record: ActivityRecord) -> Optional[int]:
    blobs = [
        record.start_state,
        record.end_state,
        "\n".join(record.prompts),
        "\n".join(call.result_summary for call in record.tool_calls),
    ]
    for blob in blobs:
        match = PR_RE.search(blob or "")
        if match:
            return int(match.group(1))
    return None


def claimed_tests_passed(record: ActivityRecord) -> bool:
    blobs: Sequence[str] = (
        record.reasoning_summary,
        "\n".join(record.prompts),
        record.end_state,
    )
    return any(TESTS_PASSED_RE.search(blob or "") for blob in blobs)


def session_success_is_claim(record: ActivityRecord) -> bool:
    for call in record.tool_calls:
        if call.name.startswith("github."):
            continue
        if call.status == "succeeded":
            return True
    return False


def is_dirty(record: ActivityRecord) -> bool:
    text = record.end_state or ""
    return any(
        marker in text
        for marker in (" modified", " untracked", " deleted")
    )


def has_edits(record: ActivityRecord) -> bool:
    return bool(record.files_changed or record.diffs or is_dirty(record))


def extract_jira_keys(record: ActivityRecord) -> tuple:
    """Explicit ticket references in prompts or Git commit messages, never diffs. Cap 3."""

    seen = []
    texts = list(record.prompts)
    if record.source == "git":
        texts.append(record.reasoning_summary)
    for prompt in texts:
        for match in JIRA_KEY_RE.finditer(prompt or ""):
            key = match.group(1)
            if key not in seen:
                seen.append(key)
            if len(seen) >= MAX_JIRA_KEYS:
                return tuple(seen)
    return tuple(seen)


def claimed_jira_done(record: ActivityRecord, key: str) -> bool:
    """Whether session text claims this exact Jira key is done."""

    return _claimed_jira_state(
        record,
        key,
        claim_re=JIRA_DONE_RE,
        negated_re=JIRA_NEGATED_DONE_RE,
    )


def claimed_jira_blocked(record: ActivityRecord, key: str) -> bool:
    """Whether session text claims this exact Jira key is blocked."""

    return _claimed_jira_state(
        record,
        key,
        claim_re=JIRA_BLOCKED_RE,
        negated_re=JIRA_NEGATED_BLOCKED_RE,
    )


def _claimed_jira_state(
    record: ActivityRecord,
    key: str,
    *,
    claim_re,
    negated_re,
) -> bool:
    needle = (key or "").upper()
    if not needle:
        return False
    for blob in (*record.prompts, record.end_state):
        text = blob or ""
        key_matches = list(JIRA_KEY_RE.finditer(text))
        if not key_matches:
            continue
        for claim in claim_re.finditer(text):
            context = text[max(0, claim.start() - 24) : claim.end() + 24]
            if negated_re.search(context):
                continue
            preceding = [
                item for item in key_matches if item.end() <= claim.start()
            ]
            nearest = (
                max(preceding, key=lambda item: item.end())
                if preceding
                else min(key_matches, key=lambda item: item.start())
            )
            if nearest.group(1).upper() == needle:
                return True
    return False
