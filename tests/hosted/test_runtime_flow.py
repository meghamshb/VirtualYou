import asyncio
from datetime import datetime, timezone

import httpx
from fastapi.testclient import TestClient
from virtualyou_workflow.coordinator import Coordinator

from virtual_you.backend.app import create_app
from virtual_you.backend.providers import DemoProvider
from virtual_you.hosted import runtime
from virtual_you.hosted.app import create_relay

from .test_relay import pair, provider, settings


def test_customer_evidence_to_approved_delivery_uses_existing_workflow(tmp_path, monkeypatch):
    async def parked(self):
        await asyncio.Event().wait()

    monkeypatch.setattr(Coordinator, "run", parked)
    monkeypatch.setattr(
        runtime,
        "create_app",
        lambda config: create_app(
            config, provider=DemoProvider(), transport=httpx.MockTransport(provider)
        ),
    )
    app = create_relay(settings(tmp_path), transport=httpx.MockTransport(provider))
    with TestClient(app, base_url="https://relay.test") as client:
        pair(client)
        assert (
            client.post(
                "/v1/setup/projects", json={"projects": ["project"], "resources": [], "people": []}
            ).status_code
            == 200
        )
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "session_id": "one",
            "source": "git",
            "end_state": "Connected the ingestion pipeline to Slack reports.",
            "timestamp_range": {"started_at": now, "ended_at": now},
        }
        assert (
            client.post(
                "/v1/activities", json={"project": "project", "records": [record]}
            ).status_code
            == 200
        )
        r = client.post("/v1/questions", json={"text": "What changed in the ingestion pipeline?"})
        assert r.status_code == 200, r.text
        draft = r.json()
        assert draft["status"] == "pending" and draft["evidence"]
        assert client.post("/v1/setup/complete").status_code == 409
        decision = client.post(
            f"/v1/drafts/{draft['id']}/decision", json={"revision": 1, "approve": True}
        )
        assert decision.status_code == 200, decision.text
        assert decision.json()["status"] == "delivered"
        assert client.post("/v1/setup/complete").status_code == 200
        assert client.get("/v1/setup").json()["step"] == "complete"
        assert (
            client.post(
                f"/v1/drafts/{draft['id']}/decision", json={"revision": 1, "approve": True}
            ).status_code
            == 409
        )


def test_github_and_jira_evidence_reaches_approved_report(tmp_path, monkeypatch):
    from urllib.parse import parse_qs, urlparse

    async def parked(self):
        await asyncio.Event().wait()

    monkeypatch.setattr(Coordinator, "run", parked)
    now = datetime.now(timezone.utc).isoformat()

    def external(request):
        if request.url.host == "auth.atlassian.com":
            return httpx.Response(
                200,
                json={
                    "access_token": "jira-private",
                    "refresh_token": "jira-refresh",
                    "expires_in": 3600,
                },
            )
        if request.url.path.endswith("accessible-resources"):
            return httpx.Response(
                200,
                json=[
                    {"id": "site", "name": "Site", "scopes": ["read:jira-work", "read:jira-user"]}
                ],
            )
        if request.url.path.endswith("/project/search"):
            return httpx.Response(200, json={"values": [{"key": "WORK", "name": "Work"}]})
        if request.url.path.endswith("/search/jql"):
            return httpx.Response(
                200,
                json={
                    "issues": [
                        {
                            "key": "WORK-1",
                            "fields": {
                                "summary": "Ingestion pipeline is connected",
                                "status": {"name": "Done"},
                                "updated": now,
                            },
                        }
                    ]
                },
            )
        if request.url.path.endswith("/commits"):
            return httpx.Response(
                200,
                json=[
                    {
                        "commit": {
                            "message": "Connected ingestion pipeline",
                            "committer": {"date": now},
                        },
                        "html_url": "https://github.com/owner/project/commit/abc",
                    }
                ],
            )
        return provider(request)

    monkeypatch.setattr(
        runtime,
        "create_app",
        lambda config: create_app(
            config, provider=DemoProvider(), transport=httpx.MockTransport(external)
        ),
    )
    app = create_relay(settings(tmp_path), transport=httpx.MockTransport(external))
    with TestClient(app, base_url="https://relay.test") as client:
        pair(client)
        for integration in ["github", "jira"]:
            url = client.post(f"/v1/integrations/{integration}/authorize").json()["url"]
            response = client.get(urlparse(url).path, follow_redirects=False)
            state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
            assert (
                "Connected."
                in client.get(
                    f"/oauth/{integration}/callback", params={"state": state, "code": "code"}
                ).text
            )
        assert client.get("/v1/resources/github").status_code == 200
        assert client.get("/v1/resources/jira?parent=site").status_code == 200
        selection = {
            "projects": ["project"],
            "people": [],
            "resources": [
                {"provider": "github", "resource": "owner/project", "project": "project"},
                {"provider": "jira", "resource": "site:WORK", "project": "project"},
            ],
        }
        assert client.post("/v1/setup/projects", json=selection).status_code == 200
        refreshed = client.post("/v1/refresh").json()
        assert all(r["ok"] for r in refreshed), refreshed
        response = client.post(
            "/v1/questions", json={"text": "What changed in the ingestion pipeline?"}
        )
        assert response.status_code == 200, response.text
        draft = response.json()
        assert {"github", "jira"}.issubset({e["source"] for e in draft["evidence"]})
        delivered = client.post(
            f"/v1/drafts/{draft['id']}/decision",
            json={"revision": draft["revision"], "approve": True},
        )
        assert delivered.json()["status"] == "delivered", delivered.text
        # Removing a selected resource retracts it from the same retrieval index.
        selection["resources"] = selection["resources"][:1]
        assert client.post("/v1/setup/projects", json=selection).status_code == 200
        next_draft = client.post(
            "/v1/questions", json={"text": "What changed in the ingestion pipeline?"}
        ).json()
        assert all(e["source"] != "jira" for e in next_draft["evidence"])
