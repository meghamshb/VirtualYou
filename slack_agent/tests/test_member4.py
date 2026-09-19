import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from virtual_you.backend.app import create_app
from virtual_you.backend.errors import ServiceError
from virtual_you.contracts.assistant import VoiceConfirm
from virtual_you.contracts.reporting import ApprovalDecision, RevisionRequest

from virtualyou_workflow.dm_replies import DMReplies
from virtualyou_workflow.listeners import register

from .test_workflow_coordinator import FakeSlack, selected


class App:
    def __init__(self):
        self.handlers = {}

    def event(self, name):
        def save(fn):
            self.handlers[name] = fn
            return fn

        return save

    action = view = shortcut = event


def ready(c, voice=True):
    selected(c)
    value = c.state.recipient("UFRIEND")
    value["reply_enabled"] = True
    c.state.save_recipient(value)
    c.credentials.installation = lambda: SimpleNamespace(user_scopes=["chat:write"])
    c.backend.store.set_metadata(
        "slack_preferences",
        {"sources": ["claude", "voice"] if voice else ["claude"], "paused": False},
    )
    return value


def message(**extra):
    return {
        "user": "UFRIEND",
        "channel": "DHUMAN",
        "channel_type": "im",
        "ts": "2000000001.01",
        "text": "What is the current status?",
        **extra,
    }


def stub_transcription(c):
    c.backend.voice.transcriber = SimpleNamespace(
        transcribe=lambda content: {
            "transcript": "Validation complete. Not deployed yet.",
            "duration_seconds": 5,
            "language": "en",
            "provider": "test:stub",
        }
    )


def test_existing_dm_route_deduplicates_and_replies_as_owner(setup):
    c, calls, network = setup
    ready(c)
    c.dm_replies = DMReplies(c, "UFRIEND")
    event = message()
    assert not c.receive_member4(event, {"team_id": "TTEAM"})

    async def run():
        c.dm_replies.receive_event(event, "TTEAM")
        c.dm_replies.receive_event(event, "TTEAM")
        # An explicit shortcut on the same message must reuse the same row.
        assert c.queue_question("UFRIEND", event["text"], event["channel"] + ":" + event["ts"])
        await c.dm_replies.prepare_one()
        with c.backend.store.connection() as db:
            rows = db.execute("SELECT * FROM slack_dm_replies").fetchall()
        assert len(rows) == 1 and rows[0]["state"] == "pending"
        assert len(c.backend.assistant.list_requests()) == 1
        assert all(data["channel"] == "DOWNER" for name, data in calls if name == "post")
        tokens = []

        def factory(token, **kwargs):
            tokens.append(token)
            return FakeSlack(calls, token)

        c.client_factory = factory
        await c.dm_replies.decide(rows[0]["id"], True)
        await c.dm_replies.decide(rows[0]["id"], True)
        assert tokens[0] == "user-token"
        assert len([d for name, d in calls if name == "post" and d["channel"] == "DHUMAN"]) == 1
        assert not network

    asyncio.run(run())


def test_uncertain_dm_goes_to_owner_inbox_without_colleague_message(setup):
    c, calls, _ = setup
    ready(c)
    c.dm_replies = DMReplies(c, "UFRIEND")
    c.dm_replies.receive_event(message(text="Should we change scope?"), "TTEAM")
    asyncio.run(c.dm_replies.prepare_one())
    item = c.backend.assistant.list_requests()[0]
    assert item["status"] == "escalated" and item["reason"] == "scope_decision"
    assert "Should we change scope?" in json.dumps(c.home())
    assert not [data for name, data in calls if name == "post"]
    with c.backend.store.connection() as db:
        assert db.execute("SELECT state FROM slack_dm_replies").fetchone()[0] == "escalated"


def test_voice_review_confirmation_and_approval_deliver_once_as_owner(setup):
    c, calls, network = setup
    ready(c)
    stub_transcription(c)
    before = c.backend.store.get_persona(c.profile_id("UFRIEND"))
    c.backend.settings.live_delivery = True

    async def run():
        note = await c.backend.voice.upload(b"synthetic", "memo")
        assert c.backend.retrieval.stats()["count"] == 1
        request = VoiceConfirm(
            expected_revision=1,
            transcript="Reviewed: not deployed.",
            project="Project A",
            recipient_id=c.profile_id("UFRIEND"),
            destination={"target": "DHUMAN", "send_as": "user"},
        )
        result = await c.backend.voice.confirm(note["id"], request)
        assert await c.backend.voice.confirm(note["id"], request) == result
        draft = c.backend.store.get_draft(result["draft_id"])
        assert draft["status"] == "pending" and draft["destination"]["target"] == "DHUMAN"
        assert c.backend.store.get_persona(c.profile_id("UFRIEND")) == before
        assert not network and not [v for k, v in calls if k == "post"]
        tokens = []

        def factory(token, **kwargs):
            tokens.append(token)
            return FakeSlack(calls, token)

        c.client_factory = factory
        c.backend.workflow.decide(
            draft["id"], ApprovalDecision(expected_revision=1, action="approve")
        )
        for _ in range(2):
            delivered = await c.backend.workflow.deliver(
                draft["id"], RevisionRequest(expected_revision=1)
            )
        assert delivered["status"] == "delivered"
        assert tokens == ["user-token"]
        assert [v["channel"] for k, v in calls if k == "post"] == ["DHUMAN"]
        assert not network

    asyncio.run(run())


def test_voice_disabled_blocks_audio_before_download_or_transcription(setup):
    c, _, _ = setup
    ready(c, voice=False)
    event = message(user="UOWNER", channel="DOWNER", files=[{"id": "F123"}])
    assert c.receive_member4(event, {"team_id": "TTEAM", "authorizations": [{"is_bot": True}]})
    assert c.state.claim() is None
    with pytest.raises(ServiceError, match="Enable Voice"):
        asyncio.run(c.backend.voice.upload(b"audio", "disabled"))


def test_voice_worker_does_not_take_approval_lane(setup):
    c, _, _ = setup
    c.state.enqueue("voice_upload", {"file_id": "F1"})
    c.state.enqueue("dm_decision", {"id": "pending", "approve": False})
    assert c.state.claim("main")["kind"] == "dm_decision"
    assert c.state.claim("voice")["kind"] == "voice_upload"


def test_mention_uses_same_conversational_engine_and_owner_destination(setup):
    c, calls, _ = setup
    ready(c)
    assert c.receive_member4(
        message(channel="CWORK", text="<@UBOT> What changed in validation?"),
        {"team_id": "TTEAM"},
        mention=True,
    )
    asyncio.run(c.process_once())
    result = c.backend.assistant.list_requests()[0]
    assert result["status"] == "draft_ready", result
    draft = c.backend.store.get_draft(result["draft_id"])
    assert draft["kind"] == "reply" and draft["destination"]["send_as"] == "user"
    assert draft["destination"]["target"] == "DHUMAN"
    assert all(v["channel"] == "DOWNER" for k, v in calls if k == "post")


@pytest.mark.parametrize("target,project", [("DBOTFRIEND", "Project A"), ("DHUMAN", "Private")])
def test_browser_voice_confirmation_cannot_bypass_slack_policy(setup, target, project):
    c, _, _ = setup
    ready(c)
    stub_transcription(c)

    async def run():
        note = await c.backend.voice.upload(b"audio", "memo")
        with pytest.raises(ServiceError):
            await c.backend.voice.confirm(
                note["id"],
                VoiceConfirm(
                    expected_revision=1,
                    recipient_id=c.profile_id("UFRIEND"),
                    project=project,
                    destination={"target": target, "send_as": "user"},
                ),
            )
        assert c.backend.store.list_drafts() == []
        assert c.backend.retrieval.stats()["count"] == 1

    asyncio.run(run())


def test_audio_file_ownership_and_host_are_checked_before_download(setup):
    c, _, _ = setup
    for file in [
        {"user": "UOTHER", "mimetype": "audio/ogg"},
        {
            "user": "UOWNER",
            "mimetype": "audio/ogg",
            "url_private": "https://files.slack.com.evil.example/a",
        },
    ]:
        c.bot = lambda: SimpleNamespace(files_info=lambda **kw: {"file": file})
        with pytest.raises(ServiceError):
            asyncio.run(c.download_voice("F123"))


def test_only_owner_can_resolve_escalation(setup):
    c, _, _ = setup
    ready(c)
    c.queue_question("UFRIEND", "Should we deploy?", "CWORK:2000000001.01")
    asyncio.run(c.process_once())
    item = c.backend.assistant.list_requests()[0]
    app = App()
    register(app, c)
    body = {"team": {"id": "TTEAM"}, "user": {"id": "UFRIEND"}, "actions": [{"value": item["id"]}]}
    app.handlers["vy_escalation_resolve"](lambda: None, body)
    assert c.backend.assistant.get(item["id"])["status"] == "escalated"
    body["user"]["id"] = "UOWNER"
    app.handlers["vy_escalation_resolve"](lambda: None, body)
    assert c.backend.assistant.get(item["id"])["status"] == "resolved"


def test_voice_scope_guard_survives_backend_only_restart(tmp_path):
    from .test_workflow_coordinator import setup as fixture_factory

    fixture = fixture_factory.__wrapped__(tmp_path)
    c, _, _ = next(fixture)
    try:
        person = ready(c)
        stub_transcription(c)

        async def create():
            note = await c.backend.voice.upload(b"audio", "memo")
            return await c.backend.voice.confirm(
                note["id"],
                VoiceConfirm(
                    expected_revision=1,
                    recipient_id=c.profile_id("UFRIEND"),
                    project="Project A",
                    destination={"target": "DHUMAN", "send_as": "user"},
                ),
            )

        result = asyncio.run(create())
        person["projects"] = ["Private"]
        c.state.save_recipient(person)
        settings = c.backend.settings
    finally:
        fixture.close()
    with TestClient(create_app(settings)) as client:
        client.headers["Authorization"] = "Bearer " + settings.api_key
        response = client.post(
            f"/api/drafts/{result['draft_id']}/decision",
            json={"expected_revision": 1, "action": "approve"},
        )
        assert (
            response.status_code == 409 and response.json()["error"]["code"] == "audience_changed"
        )


@pytest.mark.parametrize("is_bot", [False, True])
def test_owner_audio_uses_verified_bot_dm_not_single_event_authorization(setup, is_bot):
    c, _, _ = setup
    ready(c)
    stub_transcription(c)
    verified = []
    downloaded = []
    c.bot = lambda: SimpleNamespace(
        conversations_info=lambda **kwargs: (
            verified.append(kwargs["channel"]) or {"channel": {"is_im": True, "user": "UOWNER"}}
        )
    )

    async def download(file_id):
        assert verified == ["DOWNER"]
        downloaded.append(file_id)
        return b"synthetic-audio"

    c.download_voice = download
    event = message(user="UOWNER", channel="DOWNER", files=[{"id": "F123"}], subtype="file_share")
    body = {"team_id": "TTEAM", "authorizations": [{"is_bot": is_bot}]}
    assert c.receive_member4(event, body)
    job = c.state.claim("voice")
    asyncio.run(c.prepare_voice(job))
    assert downloaded == ["F123"]
    assert c.backend.voice.list_notes()[0]["status"] == "needs_review"
    assert c.backend.store.list_drafts() == []


@pytest.mark.parametrize(
    "channel", [{"is_im": False, "user": "UOWNER"}, {"is_im": True, "user": "UOTHER"}]
)
def test_owner_audio_in_other_conversations_is_rejected_before_download(setup, channel):
    c, _, _ = setup
    ready(c)
    c.bot = lambda: SimpleNamespace(conversations_info=lambda **kwargs: {"channel": channel})

    async def forbidden(file_id):
        pytest.fail("Do not download a file outside the owner's verified bot DM")

    c.download_voice = forbidden
    event = message(user="UOWNER", channel="DUNKNOWN", files=[{"id": "F123"}])
    assert c.receive_member4(event, {"team_id": "TTEAM", "authorizations": [{"is_bot": False}]})
    with pytest.raises(ServiceError, match="VirtualYou DM"):
        asyncio.run(c.prepare_voice(c.state.claim("voice")))
    assert c.backend.voice.list_notes() == []


def test_slack_generic_binary_audio_still_passes_actual_decoder(setup, monkeypatch):
    import io
    import wave

    import httpx
    from virtual_you.backend.voice import decode_audio

    c, _, _ = setup
    audio = io.BytesIO()
    with wave.open(audio, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\x00\x00" * 1600)
    c.bot = lambda: SimpleNamespace(
        files_info=lambda **kwargs: {
            "file": {
                "user": "UOWNER",
                "mimetype": "application/octet-stream",
                "name": "memo.wav",
                "url_private": "https://files.slack.com/private-audio",
                "size": len(audio.getvalue()),
            }
        }
    )
    original = httpx.AsyncClient

    def response(request):
        assert request.url.host == "files.slack.com"
        assert request.headers["Authorization"] == "Bearer bot-token"
        return httpx.Response(200, content=audio.getvalue())

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(response), **kwargs),
    )
    content = asyncio.run(c.download_voice("F123"))
    assert len(decode_audio(content)) == 1600
