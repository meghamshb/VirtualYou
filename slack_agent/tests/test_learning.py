import asyncio
import json
from types import SimpleNamespace

import pytest
from virtual_you.backend.errors import ServiceError

from virtualyou_workflow.formatting import assisted_reply
from virtualyou_workflow.learning import learning_buttons, register_learning, validate_edit

from .test_dm_replies import make_monitor


async def pending(monitor):
    await monitor.poll()
    await monitor.prepare_one()
    with monitor.c.backend.store.connection() as db:
        return db.execute("select id from slack_dm_replies").fetchone()[0]


@pytest.mark.parametrize("kind", ["message", "style", "fact", "audience"])
def test_edit_send_is_exact_once_and_never_implicitly_changes_persona(tmp_path, kind):
    mon, calls = make_monitor(tmp_path)

    async def run():
        key = await pending(mon)
        before = mon.c.backend.store.get_persona("UFRIEND")
        assert "Edit & send" in json.dumps(calls[0]["blocks"])
        await mon.decide(
            key,
            True,
            edited_text=assisted_reply("Done—tests pass. Not deployed yet."),
            edit_kind=kind,
            expected_revision=0,
        )
        await mon.decide(key, True, edited_text=assisted_reply("duplicate"), edit_kind=kind, expected_revision=1)
        assert len(calls) == 2 and calls[-1]["channel"] == "DHUMAN"
        assert calls[-1]["text"] == assisted_reply("Done—tests pass. Not deployed yet.")
        row = mon.get(key)
        assert row["state"] == "sent" and row["edit_revision"] == 1 and row["original_reply"]
        assert mon.c.backend.store.get_persona("UFRIEND") == before
        memory = mon.context.memory("DHUMAN:DHUMAN")
        assert memory.has_prior_delivery
        assert memory.delivered_evidence_refs == ()  # Edits cannot inherit draft citations.
        blocks = json.dumps(learning_buttons(mon.c, row))
        assert ("Remember this preference" in blocks) == (kind == "style")
        if kind == "audience":
            assert "Review audience policy" in blocks
        if kind == "fact":
            assert "evidence needs review" in blocks

    asyncio.run(run())


def test_stale_edit_and_secrets_cannot_send(tmp_path):
    mon, calls = make_monitor(tmp_path)

    async def run():
        key = await pending(mon)
        for text, version in [("new reply", 8), ("password=private123", 0)]:
            with pytest.raises(ServiceError):
                await mon.decide(key, True, edited_text=assisted_reply(text), expected_revision=version)
        assert mon.get(key)["state"] == "pending" and len(calls) == 1

    asyncio.run(run())


def test_shortening_suggestion_is_recipient_specific(tmp_path):
    mon, _ = make_monitor(tmp_path)
    with mon.c.backend.store.connection(write=True) as db:
        for i in range(2):
            db.execute(
                "INSERT INTO slack_dm_replies(id,recipient,channel,source_ts,prompt,reply,state,original_reply,edit_kind) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    str(i),
                    "UOTHER",
                    "DOTHER",
                    str(i),
                    "q",
                    "Done.",
                    "sent",
                    "The implementation has been successfully completed.",
                    "style",
                ),
            )
    row = {"id": "x", "recipient": "UFRIEND", "edit_kind": "style"}
    assert "Repeated shorter" not in json.dumps(learning_buttons(mon.c, row))
    row["recipient"] = "UOTHER"
    assert "Repeated shorter" in json.dumps(learning_buttons(mon.c, row))


def test_edit_modal_owner_and_submission_guards(tmp_path):
    mon, _ = make_monitor(tmp_path)
    key = asyncio.run(pending(mon))
    handlers, jobs, views, acks = {}, [], [], []
    app = SimpleNamespace(
        action=lambda name: lambda fn: handlers.update({name: fn}),
        view=lambda name: lambda fn: handlers.update({name: fn}),
    )
    mon.c.authorized = lambda body: body.get("user") == "owner"
    mon.c.dm_replies = mon
    mon.c.state.enqueue = lambda *args: jobs.append(args)
    register_learning(app, mon.c, lambda *args: "dedupe")
    client = SimpleNamespace(views_open=lambda **kwargs: views.append(kwargs["view"]))
    body = {"user": "stranger", "actions": [{"value": key}], "trigger_id": "trigger"}
    handlers["vy_dm_edit"](lambda: None, body, client)
    assert not views
    body["user"] = "owner"
    handlers["vy_dm_edit"](lambda: None, body, client)
    view = views[0]
    assert view["submit"]["text"] == "Send as me"
    view["state"] = {
        "values": {
            "message": {"value": {"value": assisted_reply("Done.")}},
            "kind": {"value": {"selected_option": {"value": "message"}}},
        }
    }
    handlers["vy_dm_edit_submit"](lambda **kw: acks.append(kw), {"user": "stranger"}, view)
    assert not jobs and acks[-1]["response_action"] == "errors"
    handlers["vy_dm_edit_submit"](lambda **kw: acks.append(kw), body, view)
    assert jobs[0][1]["edited_text"] == assisted_reply("Done.") and jobs[0][1]["edit_kind"] == "message"


@pytest.mark.parametrize("text,kind", [("", "style"), ("x" * 3001, "message"), ("fine", "unknown")])
def test_invalid_edit(text, kind):
    with pytest.raises(ServiceError):
        validate_edit(text, kind)


def test_explicit_preference_save_replay_and_undo(tmp_path):
    from virtual_you.backend.config import Settings
    from virtual_you.backend.persona import PersonaService
    from virtual_you.backend.store import Store
    from virtual_you.contracts.reporting import PersonaSeed

    mon, _ = make_monitor(tmp_path)
    store = mon.c.backend.store
    store.get_persona = Store.get_persona.__get__(store, Store)
    settings = Settings(data_dir=tmp_path / "personas")
    settings.prepare()
    mon.c.backend.persona = PersonaService(settings, store, SimpleNamespace(name="test"))
    seed = PersonaSeed(recipient_id="UFRIEND", display_name="Friend")
    first = asyncio.run(mon.c.backend.persona.create(seed))

    async def send():
        key = await pending(mon)
        await mon.decide(key, True, edited_text=assisted_reply("Done."), edit_kind="style", expected_revision=0)
        return key

    key = asyncio.run(send())
    handlers, views, acks = {}, [], []
    app = SimpleNamespace(
        action=lambda name: lambda fn: handlers.update({name: fn}),
        view=lambda name: lambda fn: handlers.update({name: fn}),
    )
    mon.c.authorized = lambda body: body.get("user") == "owner"
    mon.c.dm_replies = mon
    mon.c.state.save_recipient = lambda person: None
    register_learning(app, mon.c, lambda *args: "key")
    client = SimpleNamespace(views_open=lambda **kw: views.append(kw["view"]))
    body = {"user": "owner", "actions": [{"value": key}], "trigger_id": "trigger"}
    handlers["vy_learn"](lambda: None, body, client)
    view = views[-1]
    view["state"] = {"values": {"preference": {"value": {"selected_option": {"value": "concise"}}}}}
    handlers["vy_learn_submit"](lambda **kw: acks.append(kw), body, view)
    current = store.get_persona("UFRIEND")
    assert current["version"] == 2 and "brief, direct" in current["style"]["sentence_style"]
    assert current["style"]["tone"] == first.style.tone
    assert mon.c.state.recipient("UFRIEND")["reviewed_version"] == 2
    handlers["vy_learn_submit"](lambda **kw: acks.append(kw), body, view)
    assert store.get_persona("UFRIEND")["version"] == 2
    assert acks[-1]["view"]["title"]["text"] == "Preference not saved"
    handlers["vy_style_history"](lambda: None, body, client)
    history = views[-1]
    assert "Version 1" in json.dumps(history) and "Version 2" in json.dumps(history)
    handlers["vy_style_undo"](lambda **kw: acks.append(kw), body, history)
    assert store.get_persona("UFRIEND")["style"] == first.style.model_dump()
    assert store.get_persona("UFRIEND")["version"] == 3
    # A later re-scan respects the owner's explicit preference, including undo.
    refreshed = asyncio.run(mon.c.backend.persona.create(seed))
    assert refreshed.style.sentence_style == first.style.sentence_style


def test_concurrent_edit_and_approval_send_at_most_once(tmp_path):
    mon, calls = make_monitor(tmp_path)

    async def run():
        key = await pending(mon)
        await asyncio.gather(
            mon.decide(key, True, edited_text=assisted_reply("Edited reply."), expected_revision=0),
            mon.decide(key, True),
        )
        assert len([call for call in calls if call["channel"] == "DHUMAN"]) == 1
        assert mon.get(key)["state"] == "sent"

    asyncio.run(run())


def test_uncertain_edited_send_is_not_retried_or_learned(tmp_path):
    mon, calls = make_monitor(tmp_path)

    async def run():
        key = await pending(mon)
        slack = mon.c.bot()

        def fail(**kwargs):
            calls.append(kwargs)
            raise TimeoutError()

        slack.chat_postMessage = fail
        with pytest.raises(TimeoutError):
            await mon.decide(key, True, edited_text=assisted_reply("Done."), edit_kind="style", expected_revision=0)
        await mon.decide(key, True)
        row = mon.get(key)
        assert row["state"] == "delivery_unknown" and len(calls) == 2
        assert "Remember this preference" not in json.dumps(mon.blocks(row, "Delivery unknown."))

    asyncio.run(run())
