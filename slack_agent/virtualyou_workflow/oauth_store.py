"""Persist only this installation's owner; tokens never enter workflow/model data."""

import os
from pathlib import Path

from slack_bolt.error import BoltError
from slack_sdk.oauth.installation_store import FileInstallationStore


class OwnerInstallationStore(FileInstallationStore):
    def __init__(self, *, base_dir, owner_id, team_id):
        super().__init__(base_dir=str(base_dir), historical_data_enabled=False)
        self.owner_id, self.team_id = owner_id, team_id

    def save(self, installation):
        if (
            installation.user_id != self.owner_id
            or installation.team_id != self.team_id
            or installation.is_enterprise_install
        ):
            raise BoltError("Authorize the configured owner in the configured workspace.")
        required = {"im:read", "im:history", "users:read"}
        if not installation.user_token or not required.issubset(
            set(installation.user_scopes or [])
        ):
            raise BoltError("Slack DM history authorization was not granted.")
        super().save(installation)
        for path in Path(self.base_dir).rglob("*"):
            os.chmod(path, 0o700 if path.is_dir() else 0o600)

    def save_bot(self, bot):
        if bot.team_id != self.team_id or bot.is_enterprise_install:
            raise BoltError("Unexpected bot workspace.")
        super().save_bot(bot)
