import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from virtual_you.backend.app import create_app
from virtual_you.backend.errors import ServiceError
from virtual_you.contracts.reporting import ApprovalDecision

from virtualyou_workflow.dm_replies import DMReplies
from virtualyou_workflow.listeners import register

from .test_workflow_coordinator import selected


class App:
    def __init__(self):
        self.handlers = {}

    def event(self, name):
        def save(fn):
            self.handlers[name] = fn
            return fn

        return save

    action = view = shortcut = event


def ready(c):
    selected(c)
    value = c.state.recipient("UFRIEND")
    value["reply_enabled"] = True
    c.state.save_recipient(value)
    return value


def message(text="What is the current status?", **extra):
    return {
        "user": "UFRIEND",
        "channel": "DBOTFRIEND",
        "channel_type": "im",
        "ts": "2000000001.01",
        "text": text,
        **extra,
    }


def test_question_events_and_polling_deduplicate_without_sending(setup):
    c, calls, network = setup
    ready(c)
    event = message(channel="DHUMAN")
    assert c.receive_member4(event, {"team_id": "TTEAM"})
    assert c.receive_member4(event, {"team_id": "TTEAM"})
    poller = DMReplies(c, "UFRIEND")
    with c.backend.store.connection(write=True) as db:
        poller._insert(db, "DHUMAN", event)
    asyncio.run(poller.prepare_one())
    asyncio.run(c.process_once())
    assert c.state.claim() is None
    results = c.backend.assistant.list_requests()
    assert len(results) == 1 and results[0]["status"] == "draft_ready"
    draft = c.backend.store.get_draft(results[0]["draft_id"])
    assert draft["status"] == "pending"
    assert draft["request"]["retrieval"]["project_ids"] == ["Project A"]
    assert draft["destination"]["target"] == "DBOTFRIEND"
    assert all(kwargs["channel"] == "DOWNER" for name, kwargs in calls if name == "post")
    assert not network


@pytest.mark.parametrize(
    "extra,body",
    [
        ({"user": "UOTHER"}, {"team_id": "TTEAM"}),
        ({}, {"team_id": "OTHER"}),
        ({"bot_id": "BOTHER"}, {"team_id": "TTEAM"}),
        ({"channel": "CUNRELATED"}, {"team_id": "TTEAM"}),
        ({"subtype": "message_changed"}, {"team_id": "TTEAM"}),
    ],
)
def test_unrelated_events_do_not_create_work_questions(setup, extra, body):
    c, _, _ = setup
    ready(c)
    assert not c.receive_member4(message(**extra), body)
    assert c.state.claim() is None


def test_opt_in_and_pause_are_required(setup):
    c, _, _ = setup
    selected(c)
    assert not c.receive_member4(message(), {"team_id": "TTEAM"})
    value = c.state.recipient("UFRIEND")
    value["reply_enabled"] = True
    c.state.save_recipient(value)
    c.backend.store.set_metadata("slack_preferences", {"sources": ["claude"], "paused": True})
    assert not c.receive_member4(message(), {"team_id": "TTEAM"})


def test_mention_escalates_unknown_and_only_owner_can_resolve(setup):
    c, calls, network = setup
    ready(c)
    app = App()
    register(app, c)
    app.handlers["app_mention"](
        message("<@UBOT> When will it ship?", channel="CWORK"), {"team_id": "TTEAM"}
    )
    asyncio.run(c.process_once())
    item = c.backend.assistant.list_requests()[0]
    assert item["status"] == "escalated" and item["reason"] == "deadline"
    assert "When will it ship?" in json.dumps(c.home())
    body = {"team": {"id": "TTEAM"}, "user": {"id": "UFRIEND"}, "actions": [{"value": item["id"]}]}
    app.handlers["vy_escalation_resolve"](lambda: None, body)
    assert c.backend.assistant.get(item["id"])["status"] == "escalated"
    body["user"]["id"] = "UOWNER"
    app.handlers["vy_escalation_resolve"](lambda: None, body)
    assert c.backend.assistant.get(item["id"])["status"] == "resolved"
    assert not network and not [v for n, v in calls if n == "post"]


def test_policy_revocation_blocks_shared_http_approval_without_slack_runtime(setup):
    c, _, network = setup
    value = ready(c)
    c.queue_question("UFRIEND", "What is the current status?", "one")
    asyncio.run(c.process_once())
    item = c.backend.assistant.list_requests()[0]
    # No Coordinator callbacks are needed: the shared gate checks persisted Slack policy.
    c.backend.workflow.guards = []
    value["reply_enabled"] = False
    c.state.save_recipient(value)
    with pytest.raises(ServiceError) as caught:
        c.backend.workflow.decide(
            item["draft_id"], ApprovalDecision(expected_revision=1, action="approve")
        )
    assert caught.value.code == "audience_changed"
    assert not network


def test_queued_question_uses_original_scope_and_escalates_if_revoked(setup):
    c, calls, _ = setup
    value = ready(c)
    c.queue_question("UFRIEND", "What is the current status?", "one")
    value["projects"] = ["Private"]
    c.state.save_recipient(value)
    asyncio.run(c.process_once())
    assert c.backend.assistant.list_requests()[0]["reason"] == "audience_changed"
    assert not [v for n, v in calls if n == "post"]


def test_owner_audio_is_transcribed_then_reviewed_and_drafted(setup):
    c, calls, network = setup
    ready(c)
    c.backend.store.set_metadata(
        "slack_preferences", {"sources": ["claude", "voice"], "paused": False}
    )
    downloads = []

    async def download(file_id):
        downloads.append(file_id)
        return b"synthetic audio"

    c.download_voice = download
    c.backend.voice.transcriber = SimpleNamespace(
        transcribe=lambda content: {
            "transcript": "Finished the validation. Not deployed yet.",
            "language": "en",
            "duration_seconds": 5,
            "provider": "test:stub",
        }
    )
    event = message(
        user="UOWNER", channel="DOWNER", text="", files=[{"id": "F1234"}], subtype="file_share"
    )
    assert not c.receive_member4(event, {"team_id": "TTEAM", "authorizations": [{"is_bot": False}]})
    body = {"team_id": "TTEAM", "authorizations": [{"is_bot": True}]}
    assert c.receive_member4(event, body)
    assert c.receive_member4(event, body)
    asyncio.run(c.process_once())
    note = c.backend.voice.list_notes()[0]
    assert note["status"] == "needs_review" and downloads == ["F1234"]
    assert c.backend.store.list_drafts() == []
    app = App()
    register(app, c)
    submitted = {"user": {"id": "UOWNER"}, "team": {"id": "TTEAM"}, "trigger_id": "voice-one"}
    view = {
        "private_metadata": json.dumps({"id": note["id"], "revision": note["revision"]}),
        "state": {
            "values": {
                "transcript": {"value": {"value": "Corrected: validation complete. Not deployed."}},
                "recipient": {"value": {"selected_option": {"value": "UFRIEND"}}},
                "project": {"value": {"value": "Project A"}},
            }
        },
    }
    app.handlers["vy_voice_confirm"](lambda **kwargs: None, submitted, view)
    asyncio.run(c.process_once())
    result = c.backend.voice.get(note["id"])
    assert result["status"] == "draft_ready", c.state.latest_error()
    assert c.backend.store.get_draft(result["draft_id"])["status"] == "pending"
    assert "Not deployed" in result["transcript"]
    assert not network
    assert all(kwargs["channel"] == "DOWNER" for name, kwargs in calls if name == "post")


@pytest.mark.parametrize(
    "file,code",
    [
        ({"user": "UOTHER", "mimetype": "audio/ogg"}, "unsupported_voice_file"),
        ({"user": "UOWNER", "mimetype": "text/plain"}, "unsupported_voice_file"),
        (
            {
                "user": "UOWNER",
                "mimetype": "audio/ogg",
                "url_private": "https://evil.example/recording",
            },
            "invalid_voice_url",
        ),
        (
            {
                "user": "UOWNER",
                "mimetype": "audio/ogg",
                "url_private": "https://files.slack.com.evil.example/audio",
            },
            "invalid_voice_url",
        ),
        (
            {"user": "UOWNER", "mimetype": "audio/ogg", "size": 9 * 1024 * 1024},
            "invalid_audio_size",
        ),
    ],
)
def test_private_audio_download_rejects_untrusted_files_before_network(setup, file, code):
    c, _, _ = setup
    c.bot = lambda: SimpleNamespace(files_info=lambda **kwargs: {"file": file})
    with pytest.raises(ServiceError) as caught:
        asyncio.run(c.download_voice("F1234"))
    assert caught.value.code == code


def test_home_stays_within_slack_block_limit(setup):
    c, _, _ = setup
    ready(c)
    value = c.state.recipient("UFRIEND")
    for i in range(12):
        c.state.save_recipient({**value, "recipient": f"U{i}", "error": "Needs attention"})
    c.member4_blocks = lambda: (
        [{"type": "section", "text": {"type": "plain_text", "text": "Pending voice"}}] * 21
    )
    assert len(c.home()["blocks"]) <= 100


def test_persisted_slack_scope_is_enforced_after_http_only_restart(tmp_path):
    from .test_workflow_coordinator import setup as setup_factory

    fixture = setup_factory.__wrapped__(tmp_path)
    c, _, _ = next(fixture)
    try:
        value = ready(c)
        c.queue_question("UFRIEND", "What is the current status?", "restart-check")
        asyncio.run(c.process_once())
        result = c.backend.assistant.list_requests()[0]
        assert result["status"] == "draft_ready"
        value["projects"] = ["Private"]
        c.state.save_recipient(value)
        settings = c.backend.settings
    finally:
        fixture.close()
    with TestClient(create_app(settings)) as client:
        client.headers["Authorization"] = "Bearer " + settings.api_key
        response = client.post(
            f"/api/drafts/{result['draft_id']}/decision",
            json={
                "expected_revision": 1,
                "action": "approve",
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "audience_changed"
