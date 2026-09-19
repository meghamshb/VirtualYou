"""Pull missed GitHub events for SHAs already in the observation log."""

from typing import Optional, Sequence

from virtual_you.mcp.enrich import _snapshot_observations
from virtual_you.mcp.github import GitHubClient
from virtual_you.mcp.observations import ObservationStore


def reconcile(
    store: ObservationStore,
    client: GitHubClient,
    *,
    shas: Optional[Sequence[str]] = None,
    task_id: str = "reconcile",
    timestamp: str = "",
) -> int:
    written = 0
    for sha in shas or store.known_shas():
        snapshot = client.snapshot_for_sha(sha)
        if snapshot is None:
            continue
        written += store.extend(
            _snapshot_observations(snapshot, task_id, timestamp)
        )
    return written
