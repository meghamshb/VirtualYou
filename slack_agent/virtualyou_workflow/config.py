import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SlackSettings:
    owner_id: str
    team_id: str
    poll_seconds: float = 30
    quiet_seconds: float = 60
    minimum_interval_seconds: float = 600
    lookback_hours: float = 24
    max_history_pages: int = 40

    @classmethod
    def from_env(cls):
        value = cls(
            owner_id=os.getenv("VIRTUAL_YOU_SLACK_OWNER", ""),
            team_id=os.getenv("VIRTUAL_YOU_SLACK_TEAM", ""),
            poll_seconds=float(os.getenv("VIRTUAL_YOU_SLACK_POLL_SECONDS", "30")),
            quiet_seconds=float(os.getenv("VIRTUAL_YOU_DRAFT_QUIET_SECONDS", "60")),
            minimum_interval_seconds=float(os.getenv("VIRTUAL_YOU_DRAFT_INTERVAL_SECONDS", "600")),
            lookback_hours=float(os.getenv("VIRTUAL_YOU_REPORT_HOURS", "24")),
        )
        if not re.fullmatch(r"[UW][A-Z0-9]+", value.owner_id):
            raise ValueError(
                "Set VIRTUAL_YOU_SLACK_OWNER to your Slack member ID during initial setup."
            )
        if not re.fullmatch(r"T[A-Z0-9]+", value.team_id):
            raise ValueError("Set VIRTUAL_YOU_SLACK_TEAM to the connected Slack workspace ID.")
        if (
            min(
                value.poll_seconds,
                value.quiet_seconds,
                value.minimum_interval_seconds,
                value.lookback_hours,
            )
            <= 0
        ):
            raise ValueError("Refresh and draft timing values must be positive.")
        return value


class Credentials:
    """One owner/workspace. User OAuth tokens never enter the model or job database."""

    def __init__(self, config, installation_store=None):
        self.config, self.installation_store = config, installation_store

    def installation(self):
        if self.installation_store:
            return self.installation_store.find_installation(
                enterprise_id=None,
                team_id=self.config.team_id,
                user_id=self.config.owner_id,
                is_enterprise_install=False,
            )
        return None

    def user_token(self):
        installed = self.installation()
        return (installed.user_token if installed else None) or os.getenv("SLACK_USER_TOKEN", "")

    def bot_token(self):
        installed = self.installation()
        return (installed.bot_token if installed else None) or os.getenv("SLACK_BOT_TOKEN", "")

    def connected(self):
        return bool(self.user_token())


def private_installation_store():
    from .oauth_store import OwnerInstallationStore

    root = Path(os.getenv("VIRTUAL_YOU_DATA_DIR", ".virtual-you")) / "slack-installations"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    config = SlackSettings.from_env()
    return OwnerInstallationStore(base_dir=root, owner_id=config.owner_id, team_id=config.team_id)
