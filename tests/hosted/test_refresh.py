import time

import httpx
import pytest

from virtual_you.hosted.oauth import OAuth, OAuthFailure
from virtual_you.hosted.resources import evidence
from virtual_you.hosted.store import Vault

from .test_relay import settings


def test_rotating_refresh_saved_and_never_returned_in_identity(tmp_path):
    config = settings(tmp_path)
    vault = Vault(tmp_path / "tokens.sqlite", config.encryption_key)
    calls = []

    def send(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600},
        )

    client = OAuth(config, vault, httpx.MockTransport(send))
    vault.put(
        "integration",
        "owner:jira",
        {"access_token": "old", "refresh_token": "old-refresh", "expires_at": 1},
    )
    token = client.tokens("owner", "jira")
    assert token["refresh_token"] == "new-refresh"
    assert client.tokens("owner", "jira")["access_token"] == "new-access"
    assert len(calls) == 1
    assert b"new-refresh" not in vault.path.read_bytes()


def test_invalid_refresh_requires_reconnect(tmp_path):
    config = settings(tmp_path)
    vault = Vault(tmp_path / "tokens.sqlite", config.encryption_key)
    client = OAuth(
        config,
        vault,
        httpx.MockTransport(lambda req: httpx.Response(400, json={"error": "invalid_grant"})),
    )
    vault.put(
        "integration",
        "owner:drive",
        {"access_token": "old", "refresh_token": "old-refresh", "expires_at": 1},
    )
    with pytest.raises(OAuthFailure):
        client.tokens("owner", "drive")
    assert vault.get("integration", "owner:drive")["error"] == "expired_reconnect"


def test_expired_state_is_not_consumable(tmp_path, monkeypatch):
    config = settings(tmp_path)
    vault = Vault(tmp_path / "tokens.sqlite", config.encryption_key)
    now = time.time()
    vault.put("oauth", "state", {"private": "value"}, ttl=10)
    monkeypatch.setattr(time, "time", lambda: now + 20)
    assert vault.get("oauth", "state", consume=True) is None


def test_unchanged_remote_evidence_has_stable_snapshot(tmp_path):
    config = settings(tmp_path)
    vault = Vault(tmp_path / "tokens.sqlite", config.encryption_key)
    vault.put("integration", "owner:github", {"access_token": "private", "scope": "repo"})
    client = OAuth(
        config,
        vault,
        httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json=[
                    {
                        "commit": {
                            "message": "Fix CI",
                            "committer": {"date": "2026-09-19T10:00:00Z"},
                        },
                        "html_url": "https://github.com/org/repo/commit/1",
                    }
                ],
            )
        ),
    )
    selection = {"provider": "github", "resource": "org/repo", "project": "project"}
    a, b = evidence(client, "owner", selection), evidence(client, "owner", selection)
    assert a == b
    assert a["source"] == "github"
    assert a["timestamp_range"]["ended_at"].startswith("2026-09-19T10:00:00")
