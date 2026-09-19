import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient
from slack_bolt import BoltResponse
from virtual_you.backend.config import Settings

from app_oauth import create_http_app


def test_oauth_events_and_authenticated_voice_share_one_server(tmp_path):
    received = []

    async def run():
        await asyncio.Event().wait()

    def slack_factory(coordinator):
        return SimpleNamespace(
            dispatch=lambda request: (
                received.append(request.body) or BoltResponse(status=200, body="event accepted")
            ),
            oauth_flow=SimpleNamespace(
                handle_installation=lambda request: BoltResponse(
                    status=302, headers={"Location": "https://slack.com/oauth/v2/authorize"}
                ),
                handle_callback=lambda request: BoltResponse(status=200, body="callback accepted"),
            ),
        )

    settings = Settings(
        data_dir=tmp_path, api_key="synthetic-local-review-key-123", heartbeat_enabled=False
    )
    app = create_http_app(
        settings=settings,
        slack_factory=slack_factory,
        coordinator_factory=lambda state: SimpleNamespace(run=run),
    )
    with TestClient(app) as client:
        assert client.get("/review").status_code == 200
        assert 'id="voice"' in client.get("/review").text
        assert client.get("/api/voice").status_code == 401
        response = client.get("/slack/install", follow_redirects=False)
        assert response.status_code == 302 and response.headers["location"].startswith(
            "https://slack.com/"
        )
        assert client.get("/slack/oauth_redirect?code=synthetic").text == "callback accepted"
        assert (
            client.post("/slack/events", json={"event": {"text": "synthetic"}}).status_code == 200
        )
        assert received == [{"event": {"text": "synthetic"}}]
        assert not app.state.slack_worker.done()
        client.headers["Authorization"] = "Bearer " + settings.api_key
        assert client.get("/api/voice").json() == []
    assert app.state.slack_worker.cancelled()


def test_worker_failure_stops_acknowledgments_and_reports_unhealthy(tmp_path):
    stopped = []

    async def run():
        raise RuntimeError("synthetic worker failure")

    settings = Settings(
        data_dir=tmp_path, api_key="synthetic-local-review-key-123", heartbeat_enabled=False
    )
    app = create_http_app(
        settings=settings,
        slack_factory=lambda coordinator: SimpleNamespace(),
        coordinator_factory=lambda state: SimpleNamespace(run=run),
    )
    app.state.on_slack_worker_stopped = lambda: stopped.append(True)
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 503
        assert client.post("/slack/events", json={}).status_code == 503
        assert stopped == [True]
