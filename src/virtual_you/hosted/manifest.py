"""Operator-only Slack manifest generator: stable relay callbacks, no credentials."""

import json
import sys
from urllib.parse import urlparse

from .oauth import BOT_SCOPES, USER_SCOPES


def manifest(origin):
    parsed = urlparse(origin)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Use the HTTPS service origin.")
    origin = origin.rstrip("/")
    return {
        "display_information": {"name": "VirtualYou"},
        "features": {
            "bot_user": {"display_name": "VirtualYou", "always_online": True},
            "app_home": {
                "home_tab_enabled": True,
                "messages_tab_enabled": True,
                "messages_tab_read_only_enabled": False,
            },
        },
        "oauth_config": {
            "redirect_urls": [origin + "/oauth/slack/callback"],
            "scopes": {"bot": BOT_SCOPES.split(","), "user": USER_SCOPES.split(",")},
        },
        "settings": {
            "event_subscriptions": {
                "request_url": origin + "/slack/events",
                "bot_events": ["app_home_opened", "app_mention", "message.im"],
                "user_events": ["message.im", "message.channels", "message.groups", "message.mpim"],
            },
            "interactivity": {"is_enabled": True, "request_url": origin + "/slack/events"},
            "socket_mode_enabled": False,
            "token_rotation_enabled": True,
        },
    }


if __name__ == "__main__":
    print(json.dumps(manifest(sys.argv[1]), indent=2))
