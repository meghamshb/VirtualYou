"""HTTP/OAuth entry point; one-time user authorization is persisted for headless use."""

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.authorization.authorize_result import AuthorizeResult
from slack_bolt.oauth.oauth_settings import OAuthSettings
from slack_sdk import WebClient
from slack_sdk.oauth.state_store import FileOAuthStateStore

from listeners import register_listeners
from virtualyou_workflow.config import private_installation_store

load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=False)
logging.basicConfig(level=logging.INFO)


def create_slack_app():
    manifest = json.loads(Path(__file__).with_name("manifest.json").read_text())
    state_dir = Path(os.getenv("VIRTUAL_YOU_DATA_DIR", ".virtual-you")) / "slack-oauth-states"
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_dir.chmod(0o700)
    redirect = os.environ.get("SLACK_REDIRECT_URI", "")
    from urllib.parse import urlparse

    parsed = urlparse(redirect)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.path != "/slack/oauth_redirect"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Configure a public HTTPS SLACK_REDIRECT_URI ending /slack/oauth_redirect."
        )
    settings = OAuthSettings(
        client_id=os.environ.get("SLACK_CLIENT_ID"),
        client_secret=os.environ.get("SLACK_CLIENT_SECRET"),
        scopes=manifest["oauth_config"]["scopes"]["bot"],
        user_scopes=manifest["oauth_config"]["scopes"]["user"],
        redirect_uri=os.environ.get("SLACK_REDIRECT_URI"),
        installation_store=private_installation_store(),
        state_store=FileOAuthStateStore(expiration_seconds=600, base_dir=str(state_dir)),
    )
    fallback_token = os.environ.get("SLACK_BOT_TOKEN")
    original = settings.authorize
    if fallback_token:
        # Let the configured owner's Home show the Connect button before user OAuth.
        def authorize(*, context, enterprise_id, team_id, user_id, **kwargs):
            if team_id != os.environ.get("VIRTUAL_YOU_SLACK_TEAM"):
                return None
            result = original(
                context=context,
                enterprise_id=enterprise_id,
                team_id=team_id,
                user_id=user_id,
                **kwargs,
            )
            if result is not None:
                return result
            client = WebClient(token=fallback_token, retry_handlers=[])
            identity = client.auth_test()
            if identity.get("team_id") != team_id:
                return None
            return AuthorizeResult.from_auth_test_response(
                auth_test_response=identity, bot_token=fallback_token
            )

        settings.authorize = authorize
    app = App(signing_secret=os.environ.get("SLACK_SIGNING_SECRET"), oauth_settings=settings)
    register_listeners(app)
    return app


if __name__ == "__main__":
    create_slack_app().start(port=int(os.getenv("PORT", "3000")))
