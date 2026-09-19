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


def create_slack_app(coordinator=None):
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
    register_listeners(app, coordinator) if coordinator is not None else register_listeners(app)
    return app


def create_http_app(*, settings=None, slack_factory=None, coordinator_factory=None):
    """Serve Slack callbacks and authenticated review on one backend and event loop."""
    import asyncio
    from contextlib import asynccontextmanager

    from fastapi import Request
    from fastapi.responses import JSONResponse
    from slack_bolt.adapter.starlette.handler import to_bolt_request, to_starlette_response
    from virtual_you.backend.app import create_app

    from virtualyou_workflow.config import Credentials, SlackSettings
    from virtualyou_workflow.coordinator import Coordinator

    app = create_app(settings)
    backend_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with backend_lifespan(application):
            if coordinator_factory:
                coordinator = coordinator_factory(application.state)
            else:
                config = SlackSettings.from_env()
                coordinator = Coordinator(
                    application.state, config, Credentials(config, private_installation_store())
                )
            application.state.slack_coordinator = coordinator
            application.state.slack_app = (slack_factory or create_slack_app)(coordinator)
            worker = asyncio.create_task(coordinator.run())
            application.state.slack_worker = worker
            stopping = False

            def worker_stopped(task):
                if stopping:
                    return
                # Do not log exception payloads that might contain private Slack data.
                logging.getLogger(__name__).error("Slack listener stopped; restart the service.")
                callback = getattr(application.state, "on_slack_worker_stopped", None)
                if callback:
                    callback()

            worker.add_done_callback(worker_stopped)
            try:
                yield
            finally:
                stopping = True
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

    app.router.lifespan_context = lifespan

    @app.middleware("http")
    async def listener_health(request, call_next):
        worker = getattr(app.state, "slack_worker", None)
        if request.url.path in {"/healthz", "/slack/events"} and worker and worker.done():
            return JSONResponse({"error": "slack_listener_stopped"}, status_code=503)
        return await call_next(request)

    @app.post("/slack/events")
    async def events(request: Request):
        bolt_request = to_bolt_request(request, await request.body())
        response = await asyncio.to_thread(app.state.slack_app.dispatch, bolt_request)
        return to_starlette_response(response)

    @app.get("/slack/install")
    async def install(request: Request):
        flow = app.state.slack_app.oauth_flow
        response = await asyncio.to_thread(flow.handle_installation, to_bolt_request(request, b""))
        return to_starlette_response(response)

    @app.get("/slack/oauth_redirect")
    async def callback(request: Request):
        flow = app.state.slack_app.oauth_flow
        response = await asyncio.to_thread(flow.handle_callback, to_bolt_request(request, b""))
        return to_starlette_response(response)

    return app


if __name__ == "__main__":
    import uvicorn

    application = create_http_app()
    server = uvicorn.Server(
        uvicorn.Config(
            application,
            host=os.getenv("HOST", "127.0.0.1"),
            port=int(os.getenv("PORT", "3000")),
            workers=1,
        )
    )
    # A service supervisor can restart the listener instead of leaving HTTP falsely healthy.
    application.state.on_slack_worker_stopped = lambda: setattr(server, "should_exit", True)
    server.run()
