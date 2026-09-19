"""Parse SHAs and session claims from an already-redacted ActivityRecord."""

import re
from typing import Optional, Sequence

from virtual_you.contracts.activity import ActivityRecord, ToolCall

HEAD_SHA_RE = re.compile(r"(?im)^HEAD\s+([0-9a-f]{7,40})\b")
SHA_RE = re.compile(r"\b([0-9a-f]{7,40})\b")
TESTS_PASSED_RE = re.compile(
    r"(?i)\b(tests?\s+passed|all tests passed|ci (is )?green|checks? passed)\b"
)
PR_RE = re.compile(r"(?i)\b(?:pull request|pr)\s*#?(\d+)\b")


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


def github_tool_calls(record: ActivityRecord) -> Sequence[ToolCall]:
    return tuple(call for call in record.tool_calls if call.name.startswith("github."))
