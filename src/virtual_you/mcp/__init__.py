"""GitHub event plane: work-state observations, not LLM tool-calling."""

from virtual_you.mcp.drive import FakeDriveClient, RestDriveClient, drive_enabled, enrich_drive
from virtual_you.mcp.enrich import enrich, github_enabled
from virtual_you.mcp.followup import FollowUpAnswer, answer
from virtual_you.mcp.github import FakeGitHubClient, RestGitHubClient
from virtual_you.mcp.jira import FakeJiraClient, RestJiraClient, enrich_jira, jira_enabled
from virtual_you.mcp.observations import ObservationStore
from virtual_you.mcp.work_state import Observation, reduce_sentence

__all__ = [
    "FakeDriveClient",
    "FakeGitHubClient",
    "FakeJiraClient",
    "FollowUpAnswer",
    "Observation",
    "ObservationStore",
    "RestDriveClient",
    "RestGitHubClient",
    "RestJiraClient",
    "answer",
    "drive_enabled",
    "enrich",
    "enrich_drive",
    "enrich_jira",
    "github_enabled",
    "jira_enabled",
    "reduce_sentence",
]
