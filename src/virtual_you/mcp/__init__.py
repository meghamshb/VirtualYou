"""GitHub event plane: work-state observations, not LLM tool-calling."""

from virtual_you.mcp.enrich import enrich, github_enabled
from virtual_you.mcp.followup import FollowUpAnswer, answer
from virtual_you.mcp.github import FakeGitHubClient, RestGitHubClient
from virtual_you.mcp.observations import ObservationStore
from virtual_you.mcp.work_state import Observation, reduce_sentence

__all__ = [
    "FakeGitHubClient",
    "FollowUpAnswer",
    "Observation",
    "ObservationStore",
    "RestGitHubClient",
    "answer",
    "enrich",
    "github_enabled",
    "reduce_sentence",
]
