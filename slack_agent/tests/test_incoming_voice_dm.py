"""Incoming colleague audio stays a question, never becomes work evidence."""

import asyncio
import json

import pytest
from virtual_you.backend.errors import ServiceError

from virtualyou_workflow.dm_inbox import DMInbox

from .test_dm_replies import make_monitor


def voice_event(**changes):
    return {
        "type": "message",
        "channel_type": "im",
        "channel": "DHUMAN",
        "user": "UFRIEND",
        "ts": "2000000002.1",
        "subtype": "file_share",
        "text": "",
        "files": [
            {
                "id": "FVOICE123",
                "mimetype": "audio/mpeg",
                "name": "question.mp3",
                "url_private": "https://files.slack.com/private-do-not-persist",
                "url_private_download": "https://files.slack.com/download-do-not-persist",
                "preview": "RAW_ATTACHMENT_PREVIEW",
            }
        ],
        **changes,
    }


def configured_monitor(tmp_path):
    monitor, posts = make_monitor(tmp_path)
    prefs = {"paused": False, "sources": ["claude", "voice"]}
    monitor.c.preferences = lambda: prefs
    return monitor, posts, prefs


def rows(monitor):
    with monitor.c.backend.store.connection() as db:
        return [dict(row) for row in db.execute("select * from slack_dm_replies")]


def patch_transcriber(monkeypatch, function):
    from virtualyou_workflow import incoming_audio

    monkeypatch.setattr(incoming_audio, "transcribe_incoming_audio", function)


@pytest.mark.parametrize("caption", ["", "Quick update"])
def test_incoming_voice_transcribes_to_reviewable_question_without_creating_activity(
    tmp_path, monkeypatch, caption
):
    monitor, posts, _ = configured_monitor(tmp_path)
    transcribed = []
    transcript = "What is the current status?"

    async def transcribe(coordinator, row, file_ids):
        transcribed.append((coordinator, row["id"], file_ids))
        return transcript

    patch_transcriber(monkeypatch, transcribe)
    monkeypatch.setattr(
        monitor.c.backend.retrieval,
        "upsert",
        lambda *args, **kwargs: pytest.fail("A colleague question is not work activity"),
    )
    monitor.receive_event(voice_event(text=caption), "TTEAM")
    queued = rows(monitor)
    assert len(queued) == 1 and queued[0]["state"] == "queued"
    assert json.loads(queued[0]["audio_file_ids"]) == ["FVOICE123"]
    assert queued[0]["audio_transcribed"] == 0
    assert not monitor.context.memory("DHUMAN:DHUMAN").history

    async def run():
        await monitor.prepare_one()
        current = monitor.get(queued[0]["id"])
        assert current["state"] == "pending"
        assert current["audio_transcribed"] == 1
        assert transcript in current["prompt"]
        assert not caption or caption in current["prompt"]
        assert transcribed[0][2] == ["FVOICE123"] and len(transcribed) == 1
        assert len(posts) == 1 and posts[0]["channel"] == "DBOTUOWNER"
        review_text = json.dumps(posts[0]["blocks"]).lower()
        assert "voice" in review_text and "transcrib" in review_text
        history = monitor.context.memory("DHUMAN:DHUMAN").history
        assert any(transcript in turn["text"] and turn["role"] == "colleague" for turn in history)
        assert all(
            e["source"] != "voice" for e in json.loads(current["grounding"]).get("evidence", [])
        )
        await monitor.decide(current["id"], True)
        await monitor.decide(current["id"], True)
        delivered = [post for post in posts if post["channel"] == "DHUMAN"]
        assert len(delivered) == 1
        assert monitor.get(current["id"])["state"] == "sent"
        assert len(transcribed) == 1

    asyncio.run(run())


def test_event_and_history_duplicates_do_not_retranscribe_audio(tmp_path, monkeypatch):
    monitor, posts, _ = configured_monitor(tmp_path)
    count = []

    async def transcribe(*args):
        count.append(True)
        return "What is the current status?"

    patch_transcriber(monkeypatch, transcribe)
    event = voice_event()
    monitor.receive_event(event, "TTEAM")
    monitor.receive_event(event, "TTEAM")
    monitor.c.bot().conversations_history = lambda **kwargs: {"messages": [event]}

    async def run():
        await monitor.poll()
        assert len(rows(monitor)) == 1
        await monitor.prepare_one()
        monitor.next_prepare = 0
        monitor.receive_event(event, "TTEAM")
        monitor.next_poll = 0
        await monitor.poll()
        await monitor.prepare_one()
        assert len(rows(monitor)) == 1 and len(count) == 1
        assert len(posts) == 1 and posts[0]["channel"] == "DBOTUOWNER"

    asyncio.run(run())


def test_transcription_failure_is_safe_and_does_not_answer_colleague(tmp_path, monkeypatch):
    monitor, posts, _ = configured_monitor(tmp_path)
    notified = []
    monitor.c.publish_home = lambda: notified.append(True)
    attempts = []

    async def fail(*args):
        attempts.append(True)
        raise ServiceError("transcription_unavailable", "PRIVATE_TRANSCRIBER_DETAIL")

    patch_transcriber(monkeypatch, fail)
    monitor.receive_event(voice_event(), "TTEAM")

    async def run():
        await monitor.prepare_one()
        current = rows(monitor)[0]
        assert current["state"] == "transcription_failed"
        assert current["audio_error"] and "PRIVATE_TRANSCRIBER_DETAIL" not in current["audio_error"]
        assert all(post["channel"] != "DHUMAN" for post in posts)
        assert "PRIVATE_TRANSCRIBER_DETAIL" not in json.dumps(posts)
        assert notified or posts  # The owner gets a visible failure path.
        assert not monitor.context.memory("DHUMAN:DHUMAN").history
        monitor.next_prepare = 0
        await monitor.prepare_one()
        assert len(attempts) == 1

    asyncio.run(run())


def test_paused_queue_never_transcribes_or_sends(tmp_path, monkeypatch):
    monitor, posts, prefs = configured_monitor(tmp_path)

    async def forbidden(*args):
        pytest.fail("Paused workflow must not transcribe audio")

    patch_transcriber(monkeypatch, forbidden)
    monitor.receive_event(voice_event(), "TTEAM")
    prefs["paused"] = True
    asyncio.run(monitor.prepare_one())
    assert rows(monitor)[0]["state"] == "queued"
    assert not posts
    monitor.receive_event(voice_event(ts="2000000003.1"), "TTEAM")
    assert len(rows(monitor)) == 1


def test_disabled_voice_source_keeps_audio_queued_without_provider_use(tmp_path, monkeypatch):
    monitor, posts, prefs = configured_monitor(tmp_path)
    prefs["sources"] = ["claude"]

    async def forbidden(*args):
        pytest.fail("Voice input requires an enabled voice source")

    patch_transcriber(monkeypatch, forbidden)
    monitor.receive_event(voice_event(text="What changed?"), "TTEAM")
    asyncio.run(monitor.prepare_one())
    assert rows(monitor)[0]["state"] == "queued"
    assert not monitor.context.memory("DHUMAN:DHUMAN").history
    assert not posts


def test_pause_during_transcription_preserves_question_without_drafting(tmp_path, monkeypatch):
    monitor, posts, prefs = configured_monitor(tmp_path)
    calls = []

    async def transcribe(*args):
        calls.append(True)
        prefs["paused"] = True
        return "What is the current status?"

    patch_transcriber(monkeypatch, transcribe)
    monitor.receive_event(voice_event(), "TTEAM")

    async def run():
        await monitor.prepare_one()
        current = rows(monitor)[0]
        assert current["state"] == "queued" and current["audio_transcribed"] == 1
        assert not posts
        prefs["paused"] = False
        monitor.next_prepare = 0
        await monitor.prepare_one()
        assert rows(monitor)[0]["state"] == "pending"
        assert len(calls) == 1
        assert len(posts) == 1 and posts[0]["channel"] == "DBOTUOWNER"

    asyncio.run(run())


@pytest.mark.parametrize(
    "files",
    [
        [{"id": "FPDF123", "mimetype": "application/pdf", "name": "question.pdf"}],
        [
            {"id": "FAUDIO123", "mimetype": "audio/mpeg", "name": "one.mp3"},
            {"id": "FAUDIO456", "mimetype": "audio/mpeg", "name": "two.mp3"},
        ],
    ],
)
def test_unsupported_or_multiple_attachments_do_not_answer_caption(tmp_path, files):
    monitor, posts, _ = configured_monitor(tmp_path)
    monitor.c.state.recipients = lambda: []
    inbox = DMInbox(monitor.c)
    event = voice_event(files=files, text="What is the current status?")
    monitor.receive_event(event, "TTEAM")
    inbox.receive_event(event, "TTEAM")
    assert not rows(monitor)
    with monitor.c.backend.store.connection() as db:
        assert db.execute("select count(*) from slack_dm_inbox").fetchone()[0] == 0
    assert not posts


@pytest.mark.parametrize(
    "changes,team",
    [
        ({"user": "UOTHER"}, "TTEAM"),
        ({"user": "UOWNER"}, "TTEAM"),
        ({"bot_id": "BBOT"}, "TTEAM"),
        ({"subtype": "message_changed"}, "TTEAM"),
        ({"subtype": "message_deleted"}, "TTEAM"),
        ({"channel_type": "channel"}, "TTEAM"),
        ({"channel": "DOTHER"}, "TTEAM"),
        ({}, "TOTHER"),
    ],
)
def test_non_colleague_or_edited_voice_events_are_ignored(tmp_path, changes, team):
    monitor, _, _ = configured_monitor(tmp_path)
    monitor.receive_event(voice_event(**changes), team)
    assert not rows(monitor)


def test_plain_text_still_uses_existing_path_without_transcription(tmp_path, monkeypatch):
    monitor, posts, _ = configured_monitor(tmp_path)

    async def forbidden(*args):
        pytest.fail("Plain text must not call speech-to-text")

    patch_transcriber(monkeypatch, forbidden)
    event = voice_event(text="What is the current status?", files=[])
    event.pop("subtype")
    monitor.receive_event(event, "TTEAM")
    asyncio.run(monitor.prepare_one())
    assert rows(monitor)[0]["state"] == "pending"
    assert len(posts) == 1 and posts[0]["channel"] == "DBOTUOWNER"


def test_inbox_preserves_only_minimal_audio_metadata_and_deduplicates(tmp_path):
    monitor, _, _ = configured_monitor(tmp_path)
    coordinator = monitor.c
    coordinator.state.recipients = lambda: [
        {**coordinator.state.recipient("UFRIEND"), "recipient": "UFRIEND"}
    ]
    inbox = DMInbox(coordinator)
    event = voice_event()
    inbox.receive_event(event, "TTEAM")
    inbox.receive_event(event, "TTEAM")
    with coordinator.backend.store.connection() as db:
        saved = db.execute("select payload from slack_dm_inbox").fetchall()
    assert len(saved) == 1
    payload = json.loads(saved[0][0])
    assert payload["files"][0]["id"] == "FVOICE123"
    assert set(payload["files"][0]) <= {"id", "mimetype", "name"}
    assert "private-do-not-persist" not in saved[0][0]
    assert "RAW_ATTACHMENT_PREVIEW" not in saved[0][0]
