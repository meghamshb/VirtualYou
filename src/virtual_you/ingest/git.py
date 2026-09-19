"""Read committed project work as redacted activity, without claiming tests ran."""

from __future__ import annotations

import hashlib
from pathlib import Path

from virtual_you.contracts.activity import ActivityRecord
from virtual_you.ingest.env_secrets import discover_env_secrets
from virtual_you.ingest.redact import assert_safe_serialized, redact_value
from virtual_you.ingest.workspace import _run_git, resolve_git_root


def collect_commits(workspace: Path, limit=20):
    root = resolve_git_root(workspace)
    if root is None:
        raise ValueError("Configured project is not a Git repository")
    result = _run_git(root, "log", "-n", str(limit), "--format=%H")
    if result.returncode:
        raise ValueError("Cannot read repository history")
    secrets = discover_env_secrets(root)
    records = []
    for sha in result.stdout.splitlines():
        metadata = _run_git(root, "show", "-s", "--format=%cI%n%B", sha)
        when, _, message = metadata.stdout.partition("\n")
        patch = _run_git(
            root,
            "show",
            "--format=",
            "--no-ext-diff",
            "--no-textconv",
            "--first-parent",
            sha,
            "--",
            ".",
            ":(exclude)**/.env*",
            ":(exclude).env*",
            ":(exclude)**/soul.md",
        )
        if metadata.returncode or patch.returncode:
            raise ValueError("Cannot read commit evidence")
        # Bound storage; truncation is explicit evidence, never silently hidden.
        diff = patch.stdout
        if len(diff) > 500_000:
            diff = (
                diff[:500_000]
                + "\n[Patch truncated at 500000 characters; remaining changes not indexed.]"
            )
        payload = {
            "session_id": "git-" + hashlib.sha256((str(root) + sha).encode()).hexdigest(),
            "source": "git",
            "source_path": str(root),
            "redacted": True,
            "start_state": "Parent of commit " + sha,
            "prompts": [],
            "reasoning_summary": "Recorded commit message: " + message.strip(),
            "diffs": [diff] if diff.strip() else [],
            "files_changed": [],
            "tool_calls": [],
            "end_state": "Recorded Git commit "
            + sha
            + ": "
            + message.strip()
            + "\nA commit records code changes; test execution and deployment are not verified by this record.",
            "timestamp_range": {"started_at": when.strip(), "ended_at": when.strip()},
        }
        record = ActivityRecord.model_validate(redact_value(payload, extra_secrets=secrets))
        assert_safe_serialized(record, extra_secrets=secrets)
        records.append(record)
    return records
