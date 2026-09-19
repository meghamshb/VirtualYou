import asyncio
import json

import pytest
from virtual_you.backend.errors import ServiceError

from virtualyou_workflow.dm_replies import DMReplies
from virtualyou_workflow.self_test import configure_self_test

from .test_dm_inbox import setup_inbox
from .test_dm_replies import make_monitor

ENV = {
    "VIRTUAL_YOU_SELF_TEST_ENABLED": "true",
    "VIRTUAL_YOU_SELF_TEST_CHANNEL": "DSELF",
    "VIRTUAL_YOU_SELF_TEST_PROJECTS": "A",
}


def event(text="vy-test: What is the current status?", **updates):
    return {
        "user": "UOWNER",
        "channel": "DSELF",
        "channel_type": "im",
        "ts": "2000000001.0",
        "text": text,
        **updates,
    }


def setup_test(tmp_path):
    normal, calls = make_monitor(tmp_path)
    slack = normal.c.bot()
    slack.conversations_info = lambda channel: {
        "channel": {"id": channel, "is_im": True, "user": "UOWNER"}
    }
    router = configure_self_test(normal.c, normal, ENV)
    return router, normal, calls


def rows(router):
    with router.c.backend.store.connection() as db:
        return [dict(row) for row in db.execute("SELECT * FROM slack_dm_replies")]


def test_disabled_mode_preserves_normal_monitor_and_requires_explicit_scope(tmp_path):
    normal, _ = make_monitor(tmp_path)
    assert configure_self_test(normal.c, normal, {}) is normal
    for patch in (
        {"VIRTUAL_YOU_SELF_TEST_CHANNEL": "CCHANNEL"},
        {"VIRTUAL_YOU_SELF_TEST_PROJECTS": ""},
        {"VIRTUAL_YOU_SELF_TEST_ENABLED": "yes"},
    ):
        with pytest.raises(ValueError):
            configure_self_test(normal.c, normal, {**ENV, **patch})


@pytest.mark.parametrize(
    "updates,team",
    [
        ({"text": "An ordinary note to myself"}, "TTEAM"),
        ({"text": "VirtualYou-assisted reply\n\nvy-test: status?"}, "TTEAM"),
        ({"text": "vy-test:"}, "TTEAM"),
        ({"channel": "DHUMAN"}, "TTEAM"),
        ({"channel_type": "mpim"}, "TTEAM"),
        ({"user": "UOTHER"}, "TTEAM"),
        ({"bot_id": "BBOT"}, "TTEAM"),
        ({"app_id": "AAPP"}, "TTEAM"),
        ({"subtype": "message_changed"}, "TTEAM"),
        ({"ts": "1.0"}, "TTEAM"),
        ({"ts": "NaN"}, "TTEAM"),
        ({}, "TOTHER"),
    ],
)
def test_self_test_ignores_unrelated_automated_old_or_wrong_workspace_messages(
    tmp_path, updates, team
):
    router, _, calls = setup_test(tmp_path)
    router.receive_event(event(**updates), team)
    assert rows(router) == [] and not calls


def test_event_and_poll_deduplicate_then_approved_reply_does_not_loop(tmp_path):
    router, normal, calls = setup_test(tmp_path)
    received = event(text="vy-test: What is the current status? password=private123")
    normal.c.bot().conversations_history = lambda **kwargs: {"messages": [received]}

    async def run():
        router.receive_event(received, "TTEAM")
        router.receive_event(received, "TTEAM")
        await router.self_test.poll()
        assert len(rows(router)) == 1
        row = rows(router)[0]
        assert not row["prompt"].startswith("vy-test:") and "private123" not in row["prompt"]
        await router.self_test.prepare_one()
        pending = router.get(row["id"])
        assert pending["state"] == "pending"
        assert len(calls) == 1 and calls[0]["channel"] == "DBOTUOWNER"
        assert "Yourself" in json.dumps(calls[0]["blocks"])
        assert "neutral self-test style" in json.dumps(calls[0]["blocks"])
        assert "create/review" not in json.dumps(calls[0]["blocks"])
        await router.decide(row["id"], True)
        await router.decide(row["id"], True)
        assert len(calls) == 2 and calls[-1]["channel"] == "DSELF"
        assert calls[-1]["text"] == pending["reply"]
        assert router.get(row["id"])["state"] == "sent"
        router.receive_event(event(pending["reply"], ts="2000000002.0"), "TTEAM")
        normal.c.bot().conversations_history = lambda **kwargs: {
            "messages": [received, event(pending["reply"], ts="2000000002.0")]
        }
        router.self_test.next_poll = 0
        await router.self_test.poll()
        assert len(rows(router)) == 1

    asyncio.run(run())


def test_regular_monitor_never_accepts_owner_messages_even_if_selected(tmp_path):
    normal, _ = make_monitor(tmp_path)
    owner = DMReplies(normal.c, "UOWNER")
    with normal.c.backend.store.connection(write=True) as db:
        owner._insert(db, "DHUMAN", event())
    assert rows(normal) == []


def test_selected_colleague_keeps_original_routing(tmp_path):
    router, normal, calls = setup_test(tmp_path)
    router.receive_event(
        event(user="UFRIEND", channel="DHUMAN", text="What is the current status?"), "TTEAM"
    )

    async def run():
        await normal.prepare_one()
        row = rows(router)[0]
        assert row["recipient"] == "UFRIEND"
        await router.decide(row["id"], True)
        assert calls[-1]["channel"] == "DHUMAN"

    asyncio.run(run())


def test_self_test_also_coexists_with_all_personal_dm_inbox(tmp_path):
    inbox, people, seeds, _, _ = setup_inbox(tmp_path)
    before = json.dumps(people, sort_keys=True)
    router = configure_self_test(inbox.c, inbox, ENV)
    assert router.all_personal_dms is True
    router.receive_event(event(), "TTEAM")
    router.receive_event(
        event(user="UTWO", channel="DTWO", text="What is the current status?"), "TTEAM"
    )
    asyncio.run(inbox.route_one())
    assert {row["recipient"] for row in rows(router)} == {"UOWNER", "UTWO"}
    assert json.dumps(people, sort_keys=True) == before and not seeds


def test_wrong_self_channel_is_rejected_before_generation_or_notification(tmp_path):
    router, normal, calls = setup_test(tmp_path)
    normal.c.bot().conversations_info = lambda **kwargs: {
        "channel": {"is_im": True, "user": "UFRIEND"}
    }
    router.receive_event(event(), "TTEAM")
    with pytest.raises(ServiceError, match="owner's own Slack DM"):
        asyncio.run(router.self_test.prepare_one())
    assert rows(router)[0]["state"] == "queued" and not calls


def test_rejection_pause_and_scope_change_never_send_self_test_reply(tmp_path):
    router, normal, calls = setup_test(tmp_path)
    router.receive_event(event(), "TTEAM")

    async def run():
        await router.self_test.prepare_one()
        row = rows(router)[0]
        normal.c.preferences = lambda: {"paused": True}
        with pytest.raises(ServiceError) as paused:
            await router.decide(row["id"], True)
        assert paused.value.code == "workflow_paused"
        normal.c.preferences = lambda: {"paused": False}
        await router.decide(row["id"], False)
        await router.decide(row["id"], True)
        assert rows(router)[0]["state"] == "rejected" and len(calls) == 1

    asyncio.run(run())


def test_reenable_resets_activation_and_invalidates_older_approvals(tmp_path, monkeypatch):
    router, normal, calls = setup_test(tmp_path)
    router.receive_event(event(), "TTEAM")
    asyncio.run(router.self_test.prepare_one())
    key = rows(router)[0]["id"]
    configure_self_test(normal.c, normal, {**ENV, "VIRTUAL_YOU_SELF_TEST_ENABLED": "false"})
    monkeypatch.setattr("virtualyou_workflow.self_test.time.time", lambda: 2100000000.0)
    new_router = configure_self_test(normal.c, normal, ENV)
    new_router.receive_event(event(ts="2000000002.0"), "TTEAM")
    asyncio.run(new_router.decide(key, True))
    assert len(rows(new_router)) == 1 and rows(new_router)[0]["state"] == "self_test_disabled"
    assert len(calls) == 1
    new_router.receive_event(event(ts="2100000001.0"), "TTEAM")
    assert len(rows(new_router)) == 2


def test_out_of_scope_self_test_never_exposes_other_project_evidence(tmp_path):
    router, normal, calls = setup_test(tmp_path)
    router = configure_self_test(normal.c, normal, {**ENV, "VIRTUAL_YOU_SELF_TEST_PROJECTS": "B"})

    async def no_evidence(**kwargs):
        from virtual_you.backend.providers import UNKNOWN

        assert json.loads(kwargs["user"])["evidence"] == []
        return {"paragraphs": [{"text": UNKNOWN, "citations": []}], "search_query": ""}

    normal.c.backend.persona.provider.generate = no_evidence
    router.receive_event(event(), "TTEAM")
    asyncio.run(router.self_test.prepare_one())
    assert rows(router)[0]["state"] == "escalated" and not calls


def test_edited_self_reply_keeps_disclosure_without_offering_persona_learning(tmp_path):
    from virtualyou_workflow.formatting import assisted_reply

    router, _, calls = setup_test(tmp_path)
    router.receive_event(event(), "TTEAM")

    async def run():
        await router.self_test.prepare_one()
        row = rows(router)[0]
        edited = assisted_reply("Validation completed. This is my self-test.")
        await router.decide(row["id"], True, edited_text=edited, expected_revision=0)
        sent = router.get(row["id"])
        assert sent["state"] == "sent" and calls[-1]["text"] == edited
        blocks = router.self_test.blocks(sent, "Sent as you in the original DM.")
        assert "vy_learn" not in json.dumps(blocks)

    asyncio.run(run())


def test_self_channel_is_reverified_at_approval(tmp_path):
    router, normal, calls = setup_test(tmp_path)
    router.receive_event(event(), "TTEAM")

    async def run():
        await router.self_test.prepare_one()
        row = rows(router)[0]
        normal.c.bot().conversations_info = lambda **kwargs: {
            "channel": {"is_im": True, "user": "UFRIEND"}
        }
        with pytest.raises(ServiceError) as error:
            await router.decide(row["id"], True)
        assert error.value.code == "not_self_dm"
        assert router.get(row["id"])["state"] == "pending" and len(calls) == 1

    asyncio.run(run())
