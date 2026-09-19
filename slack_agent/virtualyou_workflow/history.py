"""Read only the owner's messages in one explicitly selected existing DM."""

from decimal import Decimal

from slack_sdk.errors import SlackApiError
from virtual_you.backend.errors import ServiceError
from virtual_you.ingest.redact import redact_text


class RetryLater(Exception):
    def __init__(self, seconds):
        self.seconds = max(1, min(float(seconds), 3600))


def slack_call(function, **kwargs):
    try:
        return function(**kwargs)
    except SlackApiError as error:
        code = error.response.get("error", "slack_request_failed")
        if code == "ratelimited" or error.response.status_code == 429:
            raise RetryLater(error.response.headers.get("Retry-After", "60")) from None
        safe = {
            "missing_scope": "Reconnect Slack to grant DM history access.",
            "invalid_auth": "Reconnect your Slack account.",
            "token_revoked": "Reconnect your Slack account.",
            "channel_not_found": "The selected conversation is no longer available.",
        }
        raise ServiceError(
            "slack_access_error", safe.get(code, "Slack could not complete this request."), 502
        ) from None


class HistoryCollector:
    def __init__(self, config):
        self.config = config

    def collect(self, client, recipient, progress, save):
        identity = slack_call(client.auth_test)
        if (
            identity.get("user_id") != self.config.owner_id
            or identity.get("team_id") != self.config.team_id
            or identity.get("bot_id")
        ):
            raise ServiceError(
                "wrong_user_token", "Authorize your own account in the configured workspace.", 403
            )
        # Cursor checkpoints contain only IDs and redacted owner-authored samples.
        progress = dict(progress)
        while not progress.get("channel"):
            pages = progress.get("list_pages", 0)
            if pages >= self.config.max_history_pages:
                raise ServiceError(
                    "history_scan_limit",
                    "DM lookup reached its scan limit. Narrow the account's DM list or adjust the server limit.",
                )
            response = slack_call(
                client.conversations_list,
                types="im",
                limit=200,
                cursor=progress.get("list_cursor") or None,
                exclude_archived=True,
            )
            match = next(
                (
                    c
                    for c in response.get("channels", [])
                    if c.get("is_im") and c.get("user") == recipient
                ),
                None,
            )
            progress.update(
                list_pages=pages + 1,
                list_cursor=response.get("response_metadata", {}).get("next_cursor", ""),
            )
            if match:
                progress["channel"] = match["id"]
            save(progress)
            if not match and not progress["list_cursor"]:
                raise ServiceError(
                    "no_existing_dm",
                    "No existing DM with this person was found. Start a conversation before creating their style profile.",
                )
        while not progress.get("complete"):
            pages = progress.get("history_pages", 0)
            if pages >= self.config.max_history_pages:
                break
            response = slack_call(
                client.conversations_history,
                channel=progress["channel"],
                limit=15,
                cursor=progress.get("history_cursor") or None,
            )
            samples = {m["ts"]: m for m in progress.get("samples", [])}
            for message in response.get("messages", []):
                if (
                    message.get("user") != self.config.owner_id
                    or message.get("bot_id")
                    or message.get("subtype")
                ):
                    continue
                text = message.get("text", "").strip()
                if not text or len(text) > 4000 or not message.get("ts"):
                    continue
                samples[message["ts"]] = {"ts": message["ts"], "text": redact_text(text)}
            ordered = sorted(samples.values(), key=lambda m: Decimal(m["ts"]), reverse=True)[:20]
            cursor = response.get("response_metadata", {}).get("next_cursor", "")
            progress.update(
                samples=ordered,
                history_cursor=cursor,
                history_pages=pages + 1,
                complete=len(ordered) >= 20 or not cursor,
            )
            save(progress)
        messages = [m["text"] for m in progress.get("samples", [])]
        return messages
