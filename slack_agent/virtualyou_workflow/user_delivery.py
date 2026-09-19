"""The same owner identity checks for DM replies and approved voice reports."""

import asyncio
from uuid import NAMESPACE_URL, uuid5

from virtual_you.backend.errors import ServiceError

from .formatting import slack_text
from .history import slack_call


def validate_owner_destination(coordinator, recipient, channel):
    installation = coordinator.credentials.installation()
    if not installation or "chat:write" not in (installation.user_scopes or []):
        raise ServiceError(
            "user_write_required", "Reconnect Slack with user chat:write permission before sending."
        )
    if not channel or channel != coordinator.state.recipient(recipient).get("human_channel"):
        raise ServiceError(
            "recipient_changed", "The original DM has changed; prepare a fresh draft."
        )
    if coordinator.preferences().get("paused"):
        raise ServiceError("workflow_paused", "Resume drafting before sending.")


async def verified_owner_client(coordinator, recipient, channel):
    validate_owner_destination(coordinator, recipient, channel)
    client = coordinator.client_factory(
        token=coordinator.credentials.user_token(), timeout=15, retry_handlers=[]
    )
    identity = await asyncio.to_thread(slack_call, client.auth_test)
    if (
        identity.get("user_id") != coordinator.config.owner_id
        or identity.get("team_id") != coordinator.config.team_id
        or identity.get("bot_id")
    ):
        raise ServiceError("wrong_user_token", "Reconnect the configured owner.")
    return client


class OwnerDMSender:
    def __init__(self, coordinator):
        self.c = coordinator

    def recipient(self, destination):
        for value in self.c.state.recipients():
            if value.get("human_channel") == destination["target"]:
                return value["recipient"]
        raise ServiceError("destination_not_allowed", "Choose a connected person's personal DM.")

    def validate(self, destination):
        if destination["platform"] != "slack" or destination.get("send_as") != "user":
            raise ServiceError(
                "invalid_destination", "Owner delivery requires a personal Slack DM."
            )
        validate_owner_destination(self.c, self.recipient(destination), destination["target"])

    async def send(self, draft, receipt):
        destination = draft["destination"]
        try:
            client = await verified_owner_client(
                self.c, self.recipient(destination), destination["target"]
            )
            self.c.backend.workflow.check_guards(draft)
        except ServiceError as error:
            return receipt.model_copy(update={"status": "failed", "error_code": error.code})
        try:
            payload = {
                "channel": destination["target"],
                "text": slack_text(draft["text"]),
                "mrkdwn": True,
                "parse": "none",
                "link_names": False,
                "unfurl_links": False,
                "unfurl_media": False,
                "client_msg_id": str(
                    uuid5(NAMESPACE_URL, f"virtual-you:{draft['id']}:{draft['revision']}")
                ),
            }
            if destination.get("thread_ts"):
                payload["thread_ts"] = destination["thread_ts"]
            # No automatic retry: a transport error may occur after Slack accepted the post.
            result = await asyncio.to_thread(client.chat_postMessage, **payload)
            if not result.get("ts"):
                raise ValueError("No receipt")
            return receipt.model_copy(update={"status": "delivered", "message_id": result["ts"]})
        except Exception:
            return receipt.model_copy(
                update={"status": "unknown", "error_code": "delivery_outcome_unknown"}
            )
