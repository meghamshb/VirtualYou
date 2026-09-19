import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from conftest import KEY, approve, new_draft, prepare
from fastapi.testclient import TestClient

from virtual_you.backend.app import create_app
from virtual_you.backend.providers import DemoProvider


def action(client, draft, name, **extra):
    return client.post(
        f"/api/drafts/{draft['id']}/{name}", json={"expected_revision": draft["revision"], **extra}
    )


def test_pending_and_rejected_drafts_cannot_deliver(client, record):
    prepare(client, record)
    draft = new_draft(client)
    assert action(client, draft, "deliver").status_code == 409
    rejected = action(client, draft, "decision", action="reject").json()
    assert rejected["status"] == "rejected"
    assert action(client, rejected, "deliver").status_code == 409
    assert action(client, rejected, "decision", action="approve").status_code == 409


def test_edit_invalidates_approval_and_stale_actions(client, record):
    prepare(client, record)
    draft = approve(client, new_draft(client))
    edited = action(client, draft, "edit", text="Reviewed updated message").json()
    assert edited["revision"] == 2 and edited["status"] == "pending"
    assert edited["approval"] is None
    assert action(client, draft, "deliver").status_code == 409
    assert action(client, edited, "deliver").status_code == 409
    approved = approve(client, edited)
    assert action(client, approved, "deliver").json()["status"] == "simulated"


def test_changing_destination_requires_new_approval(client, record):
    prepare(client, record)
    draft = approve(client, new_draft(client))
    edited = action(
        client,
        draft,
        "edit",
        text=draft["text"],
        destination={"platform": "discord", "target": "default"},
    ).json()
    assert edited["approval"] is None and edited["status"] == "pending"
    assert edited["request"]["destination"]["platform"] == "discord"


def test_regeneration_uses_latest_evidence_and_resets_approval(client, record):
    prepare(client, record)
    draft = approve(client, new_draft(client))
    record["end_state"] = "A new observation after the first draft."
    client.post("/api/activities", json=record)
    regenerated = action(client, draft, "regenerate").json()
    assert regenerated["revision"] == 2
    assert regenerated["approval"] is None
    assert "A new observation" in regenerated["text"]


def test_simulation_is_distinct_and_idempotent(client, record):
    prepare(client, record)
    draft = approve(client, new_draft(client))
    result = action(client, draft, "deliver").json()
    assert result["status"] == result["receipt"]["status"] == "simulated"
    repeated = action(client, draft, "deliver").json()
    assert repeated["receipt"] == result["receipt"]
    assert action(client, result, "edit", text="different").status_code == 409


def test_evidence_refresh_never_mutates_reviewed_text(client, record):
    prepare(client, record)
    draft = approve(client, new_draft(client))
    record["end_state"] = "new work"
    client.post("/api/activities", json=record)
    client.post("/api/refresh")
    assert client.get(f"/api/drafts/{draft['id']}").json() == draft


def test_changed_evidence_requires_fresh_draft_before_approval(client, record):
    prepare(client, record)
    draft = new_draft(client)
    record["end_state"] = "Evidence changed after generation."
    client.post("/api/activities", json=record)
    response = action(client, draft, "decision", action="approve")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "evidence_changed"


def test_changed_evidence_invalidates_approval_before_delivery(client, record):
    prepare(client, record)
    draft = approve(client, new_draft(client))
    record["end_state"] = "Evidence changed after approval."
    client.post("/api/activities", json=record)
    response = action(client, draft, "deliver")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "evidence_changed"


def test_approved_text_hash_detects_tampering(client, record):
    prepare(client, record)
    draft = approve(client, new_draft(client))
    store = client.app.state.store
    with store.connection(write=True) as db:
        draft["text"] = "Unapproved change"
        store.save_draft(db, draft, "test_tamper")
    assert action(client, draft, "deliver").json()["error"]["code"] == "approval_mismatch"


def test_live_slack_delivery_is_once_under_concurrent_clicks(settings, record):
    settings.live_delivery, settings.slack_bot_token, settings.slack_channels = (
        True,
        "test-token",
        ("demo-channel",),
    )
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={"ok": True, "ts": "123.456"})

    with TestClient(create_app(settings, transport=httpx.MockTransport(handler))) as client:
        client.headers["Authorization"] = "Bearer " + KEY
        prepare(client, record)
        draft = approve(client, new_draft(client))
        with ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(lambda _: action(client, draft, "deliver"), range(2)))
        assert sorted(r.status_code for r in responses) == [200, 409]
        assert len(calls) == 1
        assert action(client, draft, "deliver").json()["receipt"]["message_id"] == "123.456"
        assert len(calls) == 1
        import json

        payload = json.loads(calls[0].content)
        assert payload["client_msg_id"] and payload["mrkdwn"] is False


@pytest.mark.parametrize("kind", ["timeout", "server_error", "bad_json", "missing_receipt"])
def test_uncertain_sends_are_persisted_and_never_retried(settings, record, kind):
    settings.live_delivery, settings.slack_bot_token, settings.slack_channels = (
        True,
        "test-token",
        ("demo-channel",),
    )
    calls = []

    def handler(request):
        calls.append(request)
        if kind == "timeout":
            raise httpx.ReadTimeout("sensitive provider details")
        if kind == "server_error":
            return httpx.Response(503)
        if kind == "bad_json":
            return httpx.Response(200, text="not json")
        return httpx.Response(200, json={"ok": True})

    with TestClient(create_app(settings, transport=httpx.MockTransport(handler))) as client:
        client.headers["Authorization"] = "Bearer " + KEY
        prepare(client, record)
        draft = approve(client, new_draft(client))
        result = action(client, draft, "deliver").json()
        assert result["status"] == "delivery_unknown"
        assert action(client, draft, "deliver").status_code == 409
        assert len(calls) == 1
        assert "sensitive" not in str(result)
        reconciled = action(
            client,
            draft,
            "reconcile",
            outcome="confirmed_delivered",
            message_id="123.456",
            note="Verified the message in the Slack channel.",
        ).json()
        assert reconciled["status"] == "delivered"


def test_known_rejection_allows_explicit_retry(settings, record):
    settings.live_delivery, settings.slack_bot_token, settings.slack_channels = (
        True,
        "test-token",
        ("demo-channel",),
    )
    calls = []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(200, json={"ok": False, "error": "not_in_channel"})
            if len(calls) == 1
            else httpx.Response(200, json={"ok": True, "ts": "123.456"})
        )

    with TestClient(create_app(settings, transport=httpx.MockTransport(handler))) as client:
        client.headers["Authorization"] = "Bearer " + KEY
        prepare(client, record)
        draft = approve(client, new_draft(client))
        assert action(client, draft, "deliver").json()["status"] == "delivery_failed"
        assert action(client, draft, "deliver").json()["status"] == "delivered"
        assert len(calls) == 2


def test_discord_delivery_has_receipt_and_disables_mentions(settings, record):
    settings.live_delivery = True
    settings.discord_webhook_url = "https://discord.com/api/webhooks/123/fake"
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "456"})

    with TestClient(create_app(settings, transport=httpx.MockTransport(handler))) as client:
        client.headers["Authorization"] = "Bearer " + KEY
        prepare(client, record)
        draft = new_draft(client, destination={"platform": "discord", "target": "default"})
        draft = action(client, draft, "edit", text="Reviewed short message @everyone").json()
        draft = approve(client, draft)
        assert action(client, draft, "deliver").json()["receipt"]["message_id"] == "456"
        assert b'"parse":[]' in calls[0].content
        assert calls[0].url.params["wait"] == "true"


def test_restart_recovers_interrupted_delivery_as_unknown(settings, record):
    with TestClient(create_app(settings)) as client:
        client.headers["Authorization"] = "Bearer " + KEY
        prepare(client, record)
        draft = approve(client, new_draft(client))
        draft["status"] = "delivering"
        with client.app.state.store.connection(write=True) as db:
            client.app.state.store.save_draft(db, draft, "simulated_interruption")
    with TestClient(create_app(settings)) as client:
        client.headers["Authorization"] = "Bearer " + KEY
        recovered = client.get(f"/api/drafts/{draft['id']}").json()
        assert recovered["status"] == "delivery_unknown"
        assert action(client, recovered, "deliver").status_code == 409


def test_model_failure_preserves_existing_draft(client, record):
    prepare(client, record)
    draft = approve(client, new_draft(client))
    from virtual_you.backend.errors import ServiceError

    class Broken(DemoProvider):
        async def generate(self, **kwargs):
            raise ServiceError("model_unavailable", "Unavailable", 502)

    client.app.state.engine.provider = Broken()
    assert action(client, draft, "regenerate").status_code == 502
    assert client.get(f"/api/drafts/{draft['id']}").json() == draft


def test_live_destination_allowlist_blocks_before_generation(settings, record):
    settings.live_delivery, settings.slack_bot_token, settings.slack_channels = (
        True,
        "test-token",
        ("C_ALLOWED",),
    )
    with TestClient(create_app(settings)) as client:
        client.headers["Authorization"] = "Bearer " + KEY
        prepare(client, record)
        response = client.post(
            "/api/drafts", json={"recipient_id": "manager", "destination": {"target": "C_OTHER"}}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "destination_not_allowed"


def test_oversized_discord_message_fails_without_network_call(settings, record):
    settings.live_delivery = True
    settings.discord_webhook_url = "https://discord.com/api/webhooks/123/fake"
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "456"})

    with TestClient(create_app(settings, transport=httpx.MockTransport(handler))) as client:
        client.headers["Authorization"] = "Bearer " + KEY
        prepare(client, record)
        draft = new_draft(client, destination={"platform": "discord", "target": "default"})
        draft = action(client, draft, "edit", text="Long message. " * 200).json()
        draft = approve(client, draft)
        result = action(client, draft, "deliver").json()
        assert result["status"] == "delivery_failed"
        assert result["receipt"]["error_code"] == "discord_message_too_long"
        assert calls == []


def test_resumable_draft_id_cannot_overwrite_existing_draft(client, record):
    from virtual_you.backend.errors import ServiceError
    from virtual_you.contracts.reporting import DraftRequest

    prepare(client, record)
    request = DraftRequest(
        recipient_id="manager", destination={"platform": "slack", "target": "demo-channel"}
    )
    workflow = client.app.state.workflow
    first = asyncio.run(workflow.create(request, draft_id="persisted-job-id"))
    with pytest.raises(ServiceError) as caught:
        asyncio.run(workflow.create(request, draft_id="persisted-job-id"))
    assert caught.value.code == "draft_already_exists"
    assert client.app.state.store.get_draft(first["id"]) == first
