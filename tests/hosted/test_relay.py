import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from virtual_you.hosted.app import create_relay
from virtual_you.hosted.config import RelaySettings
from virtual_you.hosted.oauth import BOT_SCOPES, USER_SCOPES
from virtual_you.hosted.store import digest


class NoRuntime:
    def __init__(self, *args):
        pass

    async def close(self):
        pass


def settings(tmp_path):
    return RelaySettings(
        "https://relay.test",
        Fernet.generate_key().decode(),
        tmp_path,
        clients={
            p: ("client", "private-client-secret") for p in ["slack", "github", "jira", "drive"]
        },
        openai_key="test-model-key",
        signing_secret="test-signing-secret",
    )


def provider(request):
    path = request.url.path
    if path.endswith("oauth.v2.access"):
        return httpx.Response(
            200,
            json={
                "ok": True,
                "access_token": "private-bot-access",
                "scope": BOT_SCOPES,
                "team": {"id": "TONE"},
                "authed_user": {
                    "id": "UONE",
                    "access_token": "private-user-access",
                    "scope": USER_SCOPES,
                },
            },
        )
    if path.endswith("auth.test"):
        return httpx.Response(
            200, json={"ok": True, "team_id": "TONE", "user_id": "UONE", "user": "Owner"}
        )
    if path.endswith("/access_token"):
        return httpx.Response(200, json={"access_token": "private-github-token", "scope": "repo"})
    if path == "/user":
        return httpx.Response(200, json={"id": 10, "login": "owner"})
    if path == "/user/repos":
        return httpx.Response(200, json=[{"full_name": "owner/project"}])
    if path.endswith("conversations.open"):
        return httpx.Response(200, json={"ok": True, "channel": {"id": "DSELF"}})
    if path.endswith("chat.postMessage"):
        return httpx.Response(200, json={"ok": True, "ts": "100.01"})
    raise AssertionError(str(request.url))


def begin(client):
    pairing = client.post("/v1/devices/pair").json()
    path = urlparse(pairing["verification_uri"]).path
    response = client.post(path, content="code=" + pairing["user_code"], follow_redirects=False)
    state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
    return pairing, state


def pair(client):
    pairing, state = begin(client)
    response = client.get("/oauth/slack/callback", params={"state": state, "code": "provider-code"})
    assert "Connected." in response.text
    grant = client.post("/v1/devices/poll", json={"device_code": pairing["device_code"]}).json()
    assert grant["state"] == "connected"
    client.headers["Authorization"] = "Bearer " + grant["credential"]
    return pairing, grant


@pytest.fixture
def relay(tmp_path):
    app = create_relay(
        settings(tmp_path), transport=httpx.MockTransport(provider), runtimes_factory=NoRuntime
    )
    with TestClient(app, base_url="https://relay.test") as client:
        yield app, client


def test_pair_is_single_use_and_tokens_never_in_status(relay):
    app, client = relay
    pairing, grant = pair(client)
    assert (
        client.post("/v1/devices/poll", json={"device_code": pairing["device_code"]}).json()[
            "state"
        ]
        == "expired"
    )
    status = client.get("/v1/setup").json()
    assert status["integrations"][0]["connected"]
    assert "private-" not in json.dumps(status)
    assert "private-user-access" not in app.state.vault.path.read_bytes().decode(errors="ignore")
    assert client.delete("/v1/device").status_code == 200
    assert client.get("/v1/setup").status_code == 401


def test_wrong_code_does_not_authorize(relay):
    app, client = relay
    p = client.post("/v1/devices/pair").json()
    r = client.post(
        urlparse(p["verification_uri"]).path, content="code=WRONG", follow_redirects=False
    )
    assert "did not match" in r.text
    assert (
        client.post("/v1/devices/poll", json={"device_code": p["device_code"]}).json()["state"]
        == "pending"
    )


@pytest.mark.parametrize("failure", ["state", "cookie", "cancel"])
def test_oauth_failures_do_not_create_devices(relay, failure):
    app, client = relay
    p, state = begin(client)
    if failure == "cookie":
        client.cookies.clear()
    r = client.get(
        "/oauth/slack/callback",
        params={
            "state": "wrong" if failure == "state" else state,
            "code": "code",
            "error": "access_denied" if failure == "cancel" else "",
        },
    )
    assert "Connected." not in r.text
    assert not app.state.vault.keys("device")


def test_other_device_cannot_read_account(relay):
    app, client = relay
    pair(client)
    client.headers["Authorization"] = "Bearer unrelated-credential"
    assert client.get("/v1/setup").status_code == 401


def test_optional_provider_real_exchange_and_state_replay(relay):
    app, client = relay
    pair(client)
    url = client.post("/v1/integrations/github/authorize").json()["url"]
    redirect = client.get(urlparse(url).path, follow_redirects=False)
    params = parse_qs(urlparse(redirect.headers["location"]).query)
    assert params["code_challenge_method"] == ["S256"]
    callback = "/oauth/github/callback?code=code&state=" + params["state"][0]
    assert "Connected." in client.get(callback).text
    assert "could not be verified" in client.get(callback).text
    github = client.get("/v1/setup").json()["integrations"][1]
    assert github["connected"] and github["label"] == "owner"


def test_missing_slack_user_scopes(tmp_path):
    def missing(request):
        response = provider(request)
        if request.url.path.endswith("oauth.v2.access"):
            value = response.json()
            value["authed_user"]["scope"] = "im:read"
            return httpx.Response(200, json=value)
        return response

    app = create_relay(
        settings(tmp_path), transport=httpx.MockTransport(missing), runtimes_factory=NoRuntime
    )
    with TestClient(app, base_url="https://relay.test") as client:
        p, state = begin(client)
        assert (
            "missing user scopes"
            in client.get("/oauth/slack/callback", params={"state": state, "code": "code"}).text
        )
        assert not app.state.vault.keys("device")


def test_reconnect_cannot_change_slack_owner(relay):
    app, client = relay
    _, grant = pair(client)
    old = app.state.vault.get("device", digest(grant["credential"]))
    app.state.vault.put("device", digest(grant["credential"]), dict(old, account="other-owner"))
    url = client.post("/v1/integrations/slack/authorize").json()["url"]
    r = client.get(urlparse(url).path, follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    assert (
        "wrong workspace or user"
        in client.get("/oauth/slack/callback", params={"state": state, "code": "code"}).text
    )


def test_raw_record_fields_are_rejected(relay):
    app, client = relay
    _, grant = pair(client)
    account = app.state.vault.get("device", digest(grant["credential"]))["account"]
    app.state.vault.put("setup", account, {"projects": ["project"]})
    record = {
        "session_id": "one",
        "source": "git",
        "diffs": ["raw patch"],
        "timestamp_range": {
            "started_at": "2026-09-20T00:00:00Z",
            "ended_at": "2026-09-20T00:00:00Z",
        },
    }
    assert (
        client.post("/v1/activities", json={"project": "project", "records": [record]}).status_code
        == 422
    )
    assert (
        client.post("/v1/activities", json={"project": "other", "records": []}).status_code == 403
    )


@pytest.mark.parametrize("which", ["jira", "drive"])
def test_jira_and_drive_real_oauth_adapters(tmp_path, which):
    from virtual_you.hosted.oauth import PROVIDERS

    def external(request):
        if request.url.host == "auth.atlassian.com" and request.url.path == "/oauth/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "jira-private",
                    "refresh_token": "jira-refresh",
                    "expires_in": 3600,
                    "scope": PROVIDERS["jira"][2],
                },
            )
        if request.url.path.endswith("accessible-resources"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "site-1",
                        "name": "Test site",
                        "scopes": ["read:jira-work", "read:jira-user"],
                    }
                ],
            )
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(
                200,
                json={
                    "access_token": "drive-private",
                    "refresh_token": "drive-refresh",
                    "scope": PROVIDERS["drive"][2],
                    "expires_in": 3600,
                },
            )
        if request.url.path == "/drive/v3/about":
            return httpx.Response(200, json={"user": {"displayName": "Test owner"}})
        return provider(request)

    app = create_relay(
        settings(tmp_path), transport=httpx.MockTransport(external), runtimes_factory=NoRuntime
    )
    with TestClient(app, base_url="https://relay.test") as client:
        pair(client)
        launch_url = client.post(f"/v1/integrations/{which}/authorize").json()["url"]
        redirect = client.get(urlparse(launch_url).path, follow_redirects=False)
        params = parse_qs(urlparse(redirect.headers["location"]).query)
        assert (
            "Connected."
            in client.get(
                f"/oauth/{which}/callback", params={"code": "code", "state": params["state"][0]}
            ).text
        )
        item = next(i for i in client.get("/v1/setup").json()["integrations"] if i["id"] == which)
        assert item["connected"]


def test_readiness_blocks_pairing_when_essential_services_missing(tmp_path):
    config = settings(tmp_path)
    config.openai_key = ''
    config.signing_secret = ''
    with TestClient(create_relay(config, runtimes_factory=NoRuntime)) as client:
        assert client.get('/healthz').status_code == 200
        readiness = client.get('/readyz')
        assert readiness.status_code == 503
        assert readiness.json()['checks'] == {'slack_oauth': True, 'slack_events': False, 'model': False}
        assert client.post('/v1/devices/pair').status_code == 409


def test_optional_integrations_do_not_block_slack_onboarding(tmp_path):
    config = settings(tmp_path)
    config.clients = {'slack': config.clients['slack']}
    with TestClient(create_relay(config, runtimes_factory=NoRuntime)) as client:
        assert client.get('/readyz').json()['ready'] is True
        assert client.post('/v1/devices/pair').status_code == 200
