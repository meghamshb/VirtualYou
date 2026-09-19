import asyncio
import json

import httpx
import pytest
from conftest import KEY
from fastapi.testclient import TestClient

from virtual_you.backend.app import create_app
from virtual_you.backend.config import Settings
from virtual_you.backend.errors import ServiceError
from virtual_you.backend.providers import HttpProvider


def test_api_requires_auth_but_health_and_ui_are_public(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/").status_code == 200
        assert client.get("/github/status").status_code == 200
        assert client.get("/github/status").json()["connected"] is False
        assert client.get("/api/status").status_code == 401
        client.headers["Authorization"] = "Bearer wrong"
        assert client.get("/api/personas").status_code == 401
        client.headers["Authorization"] = "Bearer " + KEY
        assert client.get("/api/status").status_code == 200


def test_github_authorize_redirects_without_api_key(settings, monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "iv1client")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "supersecret")
    monkeypatch.setenv("GITHUB_OAUTH_REDIRECT", "http://127.0.0.1:8000/github/callback")
    with TestClient(create_app(settings), follow_redirects=False) as client:
        response = client.get("/github/authorize")
        assert response.status_code == 302
        location = response.headers["location"]
        assert "github.com/login/oauth/authorize" in location
        assert "client_id=iv1client" in location
        assert "supersecret" not in location
        html = client.get("/").text
        assert "Authorize GitHub" in html


def test_validation_errors_never_echo_sensitive_inputs(client):
    response = client.post(
        "/api/personas",
        json={
            "recipient_id": "../../bad",
            "display_name": "secret-name",
            "messages": ["sk-proj-private-example"],
        },
    )
    assert response.status_code == 422
    assert "sk-proj-private-example" not in response.text and "secret-name" not in response.text


def test_oversized_body_rejected_before_parsing(client):
    response = client.post("/api/activities", content=b"x" * 1_000_001)
    assert response.status_code == 413


def test_local_key_is_generated_private_and_reused(tmp_path):
    first = Settings(data_dir=tmp_path).prepare()
    second = Settings(data_dir=tmp_path).prepare()
    assert len(first.api_key) >= 24 and first.api_key == second.api_key
    assert (tmp_path / "admin.key").stat().st_mode & 0o777 == 0o600


def test_second_server_on_same_database_is_rejected(settings):
    with TestClient(create_app(settings)):
        with pytest.raises(RuntimeError, match="one backend worker"):
            with TestClient(create_app(settings)):
                pass


@pytest.mark.parametrize("provider", ["openai", "ollama"])
def test_real_provider_adapter_request_and_response_contract(tmp_path, provider):
    settings = Settings(
        data_dir=tmp_path, provider=provider, model="test-model", openai_api_key="test-key"
    )
    calls = []

    def handler(request):
        calls.append(request)
        content = json.dumps({"tone": "direct"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}]}
            if provider == "openai"
            else {"message": {"content": content}},
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await HttpProvider(settings, client).generate(
                task="persona", system="style", user="examples", schema={"type": "object"}
            )
            assert result == {"tone": "direct"}

    asyncio.run(scenario())
    sent = json.loads(calls[0].content)
    assert sent["model"] == "test-model" and sent["messages"][0]["role"] == "system"
    if provider == "openai":
        assert calls[0].headers["Authorization"] == "Bearer test-key"
    else:
        assert sent["stream"] is False and sent["format"] == {"type": "object"}


def test_provider_error_is_safe_and_has_no_automatic_demo_fallback(tmp_path):
    settings = Settings(
        data_dir=tmp_path, provider="openai", model="test-model", openai_api_key="private"
    )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(401, text="private provider details")
            )
        ) as client:
            with pytest.raises(ServiceError) as failure:
                await HttpProvider(settings, client).generate(
                    task="draft", system="", user="", schema={}
                )
            assert failure.value.code == "model_unavailable"
            assert "private" not in str(failure.value)

    asyncio.run(scenario())


def test_insecure_remote_feed_rejected(tmp_path):
    with pytest.raises(ValueError, match="HTTPS"):
        Settings(data_dir=tmp_path, activity_feed_url="http://remote.example/records").prepare()
