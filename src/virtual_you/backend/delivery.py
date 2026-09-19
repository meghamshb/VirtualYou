from __future__ import annotations

import html
from uuid import NAMESPACE_URL, uuid5

import httpx

from virtual_you.backend.errors import ServiceError
from virtual_you.contracts.reporting import DeliveryReceipt, utcnow


class DeliveryGateway:
    def __init__(self, settings, client):
        self.settings, self.client = settings, client
        self.user_sender = None

    def validate_destination(self, destination):
        platform, target = destination["platform"], destination["target"]
        if destination.get("thread_ts") and platform != "slack":
            raise ServiceError(
                "invalid_destination", "Thread timestamps are supported only for Slack.", 422
            )
        if destination.get("send_as") == "user":
            if platform != "slack" or self.user_sender is None:
                raise ServiceError(
                    "user_delivery_unavailable",
                    "Start the connected Slack agent to send as you.",
                    503,
                )
            self.user_sender.validate(destination)
            return
        if not self.settings.live_delivery:
            return
        if platform == "slack":
            if target not in self.settings.slack_channels:
                raise ServiceError(
                    "destination_not_allowed",
                    "Slack destination is not in the configured allowlist.",
                    422,
                )
            if not self.settings.slack_bot_token:
                raise ServiceError(
                    "delivery_not_configured",
                    "Configure SLACK_BOT_TOKEN before live delivery.",
                    503,
                )
        elif target != "default" or not self.settings.discord_webhook_url:
            raise ServiceError(
                "delivery_not_configured",
                "Discord requires the configured webhook and target 'default'.",
                503,
            )

    async def send(self, draft):
        destination = draft["destination"]
        receipt = DeliveryReceipt(
            draft_id=draft["id"],
            revision=draft["revision"],
            status="simulated",
            platform=destination["platform"],
            target=destination["target"],
            attempted_at=utcnow(),
        )
        if not self.settings.live_delivery:
            return receipt
        platform = destination["platform"]
        if destination.get("send_as") == "user":
            if self.user_sender is None:
                return receipt.model_copy(
                    update={"status": "failed", "error_code": "user_delivery_unavailable"}
                )
            return await self.user_sender.send(draft, receipt)
        if platform == "discord" and len(draft["text"]) > 2000:
            return receipt.model_copy(
                update={"status": "failed", "error_code": "discord_message_too_long"}
            )
        try:
            if platform == "slack":
                # Escape Slack's special mention/link syntax; no mass mentions from model output.
                payload = {
                    "channel": destination["target"],
                    "text": html.escape(draft["text"], quote=False),
                    "mrkdwn": False,
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
                response = await self.client.post(
                    "https://slack.com/api/chat.postMessage",
                    json=payload,
                    headers={"Authorization": "Bearer " + self.settings.slack_bot_token},
                )
            else:
                response = await self.client.post(
                    self.settings.discord_webhook_url,
                    params={"wait": "true"},
                    json={"content": draft["text"], "allowed_mentions": {"parse": []}},
                )
            if response.status_code == 429:
                return receipt.model_copy(update={"status": "failed", "error_code": "rate_limited"})
            if 400 <= response.status_code < 500:
                return receipt.model_copy(
                    update={"status": "failed", "error_code": "provider_rejected"}
                )
            if response.status_code >= 300:
                return receipt.model_copy(
                    update={"status": "unknown", "error_code": "provider_response_uncertain"}
                )
            result = response.json()
            if platform == "slack" and result.get("ok") is not True:
                known_rejections = {
                    "not_authed",
                    "invalid_auth",
                    "account_inactive",
                    "token_revoked",
                    "missing_scope",
                    "channel_not_found",
                    "not_in_channel",
                    "is_archived",
                    "msg_too_long",
                    "no_text",
                    "ratelimited",
                }
                status = "failed" if result.get("error") in known_rejections else "unknown"
                return receipt.model_copy(
                    update={
                        "status": status,
                        "error_code": "slack_rejected"
                        if status == "failed"
                        else "slack_result_uncertain",
                    }
                )
            message_id = result.get("ts") if platform == "slack" else result.get("id")
            if not message_id:
                raise ValueError("Missing receipt")
            return receipt.model_copy(update={"status": "delivered", "message_id": str(message_id)})
        except (httpx.HTTPError, ValueError, TypeError):
            # A timeout can happen AFTER the message reached the destination. Never auto-retry.
            return receipt.model_copy(
                update={"status": "unknown", "error_code": "delivery_outcome_unknown"}
            )
