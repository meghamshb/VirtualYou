import asyncio
import json
from types import SimpleNamespace

import pytest
from virtual_you.backend.errors import ServiceError
from virtual_you.contracts.reporting import utcnow

from virtualyou_workflow.groups import GroupConversations


@pytest.fixture
def group(setup):
    c, _, _ = setup
    c.backend.settings.live_delivery = True
    c.backend.retrieval.assign_project(["new-work"], "virtualyou")
    c.credentials.installation = lambda: SimpleNamespace(
        user_scopes=[
            "chat:write",
            "channels:read",
            "channels:history",
            "groups:read",
            "groups:history",
            "mpim:read",
            "mpim:history",
            "users:read",
        ]
    )
    calls, prompts = [], []

    class Slack:
        members = ["UOWNER", "UASKER"]
        shared = False
        bot_requester = False
        has_more = False
        fail_send = False

        def auth_test(self):
            return {"user_id": "UOWNER", "team_id": "TTEAM"}

        def conversations_info(self, **kw):
            return {
                "channel": {
                    "is_channel": kw["channel"].startswith("C"),
                    "is_mpim": kw["channel"].startswith("G"),
                    "is_ext_shared": self.shared,
                }
            }

        def conversations_members(self, **kw):
            return {"members": self.members}

        def users_info(self, **kw):
            return {"user": {"is_bot": self.bot_requester}}

        def conversations_replies(self, **kw):
            return {
                "messages": [{"user": "UASKER", "text": "What is the status?", "ts": "100.1"}],
                "response_metadata": {"next_cursor": "more" if self.has_more else ""},
            }

        def chat_postMessage(self, **kw):
            calls.append(kw)
            if self.fail_send:
                raise TimeoutError()
            return {"ts": "200.1"}

    slack = Slack()
    c.client_factory = lambda **kw: slack

    class Model:
        eligible = True

        async def generate(self, **kw):
            data = json.loads(kw["user"])
            prompts.append((kw["task"], data))
            if kw["task"] == "group_auto_review":
                return {"eligible": self.eligible, "reason": "checked"}
            e = next(e for e in data["evidence"] if e["field"] == "end_state")
            return {
                "paragraphs": [
                    {"text": e["text"], "citations": [{"evidence_id": e["evidence_id"]}]}
                ],
                "search_query": "",
            }

    model = Model()
    c.backend.engine.provider = model
    c.backend.store.set_metadata("slack_preferences", {"sources": ["claude"], "paused": False})
    return c.groups, slack, calls, prompts, model


def configure(g, automatic=False, channel="CTEST"):
    old = g.policy(channel)
    return g.configure(
        "UOWNER",
        channel,
        ["virtualyou"],
        ["claude"],
        "formal",
        automatic,
        expected_revision=old["revision"] if old else None,
        expected_auto_epoch=old["auto_epoch"] if old else None,
    )


def question(g, text="What is the current status?", channel="CTEST", ts="100.1"):
    intent = g.intent(
        {
            "team": {"id": "TTEAM"},
            "user": {"id": "UASKER"},
            "channel": {"id": channel},
            "message": {"user": "UASKER", "text": text, "ts": ts, "thread_ts": "100.0"},
        }
    )
    return g.enqueue(intent["id"], "UASKER", "UOWNER", text)


@pytest.mark.parametrize("channel", ["CTEST", "GTEST"])
def test_explicit_question_scoped_context_and_owner_approval(group, channel):
    g, slack, calls, prompts, _ = group
    configure(g, channel=channel)
    now = utcnow()
    g.c.backend.retrieval.upsert(
        {
            "session_id": "private",
            "source": "claude",
            "redacted": True,
            "end_state": "TOP PRIVATE PROJECT FACT",
            "timestamp_range": {"started_at": now, "ended_at": now},
        }
    )
    g.c.backend.retrieval.assign_project(["private"], "private-project")

    async def run():
        key = question(g, channel=channel)
        await g.prepare(key)
        assert g.get(key)["state"] == "pending" and not calls
        assert "TOP PRIVATE" not in json.dumps(prompts)
        assert prompts[0][1]["thread_context"][0]["text"] == "What is the status?"
        assert prompts[0][1]["style_only"]["greeting"] == ""
        assert question(g, channel=channel) == key
        with pytest.raises(ServiceError):
            await g.send(key, actor="UASKER")
        await g.send(key, actor="UOWNER")
        await g.send(key, actor="UOWNER")
        assert (
            len(calls) == 1 and calls[0]["channel"] == channel and calls[0]["thread_ts"] == "100.0"
        )
        assert calls[0]["text"].startswith("VirtualYou for UOWNER · owner-approved")
        assert g.get(key)["state"] == "sent"

    asyncio.run(run())


def test_auto_send_audit_and_group_watermark(group):
    g, _, calls, _, _ = group
    configure(g, True)

    async def run():
        key = question(g)
        await g.prepare(key)
        assert g.get(key)["state"] == "sent" and g.get(key)["automatic"]
        assert g.policy("CTEST")["last_update"] == g.get(key)["cutoff"]
        with g.c.backend.store.connection() as db:
            audit = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM group_audit WHERE event='delivery_outcome'"
                )
            ]
        assert audit[0]["result"]["evidence"] and audit[0]["requester"] == "UASKER"
        later = question(g, "Summarize progress since the last update", ts="101.1")
        await g.prepare(later)
        assert g.get(later)["since"] == g.get(key)["cutoff"]
        assert len(calls) == 1  # No new facts after the actual shared update.

    asyncio.run(run())


def test_disable_during_generation_revokes_automatic_send(group):
    g, _, calls, _, model = group
    configure(g, True)
    original = model.generate

    async def generate(**kw):
        result = await original(**kw)
        if kw["task"] == "group_auto_review":
            g.disable_auto("UOWNER", "CTEST")
        return result

    model.generate = generate

    async def run():
        key = question(g)
        await g.prepare(key)
        assert g.get(key)["state"] == "pending" and not calls
        await g.send(key, actor="UOWNER")
        assert len(calls) == 1 and not g.get(key)["automatic"]

    asyncio.run(run())


def test_off_invalidates_queued_enable_settings(group):
    g, *_ = group
    old = configure(g)
    g.disable_auto("UOWNER", "CTEST")
    with pytest.raises(ServiceError):
        g.configure(
            "UOWNER",
            "CTEST",
            ["virtualyou"],
            ["claude"],
            "formal",
            True,
            expected_revision=old["revision"],
            expected_auto_epoch=old["auto_epoch"],
        )
    assert not g.policy("CTEST")["automatic"]


@pytest.mark.parametrize("kind", ["membership", "scope", "source", "evidence", "shared"])
def test_boundary_rechecked_at_send(group, kind):
    g, slack, calls, _, _ = group
    configure(g)

    async def run():
        key = question(g)
        await g.prepare(key)
        if kind == "membership":
            slack.members.append("UNEW")
        elif kind == "shared":
            slack.shared = True
        elif kind == "scope":
            configure(g)
        elif kind == "source":
            g.c.backend.retrieval.assign_project(["new-work"], "private-only")
        else:
            now = utcnow()
            g.c.backend.retrieval.upsert(
                {
                    "session_id": "new-work",
                    "source": "claude",
                    "redacted": True,
                    "end_state": "Different facts",
                    "timestamp_range": {"started_at": now, "ended_at": now},
                }
            )
        with pytest.raises(ServiceError):
            await g.send(key, actor="UOWNER")
        assert not calls

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["bot", "context", "uncertain", "judgment"])
def test_fail_closed_to_owner_review(group, failure):
    g, slack, calls, _, model = group
    configure(g, True)
    if failure == "bot":
        slack.bot_requester = True
    if failure == "context":
        slack.has_more = True
    if failure == "uncertain":
        model.eligible = False

    async def run():
        key = question(
            g, "Should we deploy today?" if failure == "judgment" else "What is the current status?"
        )
        await g.prepare(key)
        assert not calls and g.get(key)["state"] in {"needs_review", "pending"}

    asyncio.run(run())


def test_owner_target_disabled_scope_and_self_loop(group):
    g, *_ = group
    with pytest.raises(ServiceError):
        question(g)
    with pytest.raises(ServiceError):
        g.configure("UASKER", "CTEST", ["virtualyou"], ["claude"], "formal")
    configure(g)
    with pytest.raises(ServiceError):
        g.disable_auto("UASKER", "CTEST")
    with pytest.raises(ServiceError):
        question(g, "VirtualYou for UOWNER · automatic")
    intent = g.intent(
        {
            "team": {"id": "TTEAM"},
            "user": {"id": "UASKER"},
            "channel": {"id": "CTEST"},
            "message": {"ts": "100.1", "text": "status"},
        }
    )
    with pytest.raises(ServiceError):
        g.enqueue(intent["id"], "UASKER", "UOTHER", "status")
    with pytest.raises(ServiceError):
        g.enqueue(intent["id"], "UOTHER", "UOWNER", "status")


def test_ambiguous_delivery_not_retried_after_restart(group):
    g, slack, calls, _, _ = group
    configure(g)
    slack.fail_send = True

    async def run():
        key = question(g)
        await g.prepare(key)
        await g.send(key, actor="UOWNER")
        assert g.get(key)["state"] == "delivery_unknown"
        restored = GroupConversations(g.c)
        await restored.send(key, actor="UOWNER")
        assert len(calls) == 1

    asyncio.run(run())


def test_auto_off_during_final_membership_check(group):
    g, slack, calls, _, _ = group
    configure(g, True)

    async def run():
        key = question(g)
        # Prepare under approval so we can exercise the final automatic dispatch gate.
        g.disable_auto("UOWNER", "CTEST")
        await g.prepare(key)
        row = g.get(key)
        row["auto_eligible"] = True
        policy = g.policy("CTEST")
        policy["automatic"] = True
        row["auto_epoch"] = policy["auto_epoch"]
        with g.c.backend.store.connection(write=True) as db:
            db.execute(
                "UPDATE group_policies SET payload=? WHERE channel=?", (json.dumps(policy), "CTEST")
            )
        g.save(row)
        original = slack.conversations_members

        def members(**kw):
            g.disable_auto("UOWNER", "CTEST")
            return original(**kw)

        slack.conversations_members = members
        await g.send(key, automatic=True)
        assert not calls and g.get(key)["state"] == "pending"

    asyncio.run(run())


def test_slack_shortcut_and_owner_only_controls(group):
    from virtualyou_workflow.group_views import register_groups

    g, _, _, _, _ = group
    configure(g)
    handlers, jobs, views, acks = {}, [], [], []
    app = SimpleNamespace(
        shortcut=lambda key: lambda fn: handlers.update({key: fn}),
        action=lambda key: lambda fn: handlers.update({key: fn}),
        view=lambda key: lambda fn: handlers.update({key: fn}),
    )
    g.c.state.enqueue = lambda *args: jobs.append(args)
    g.c.publish_home = lambda: None
    register_groups(app, g.c, lambda *args: "event")
    body = {
        "team": {"id": "TTEAM"},
        "user": {"id": "UASKER"},
        "channel": {"id": "CTEST"},
        "message": {"user": "UASKER", "ts": "100.1", "text": "status"},
        "trigger_id": "trigger",
    }
    client = SimpleNamespace(views_open=lambda **kw: views.append(kw["view"]))
    handlers["vy_group_ask"](lambda: None, body, client)
    view = views[-1]
    view["state"] = {
        "values": {
            "owner": {"value": {"selected_user": "UOWNER"}},
            "question": {"value": {"value": "What is the current status?"}},
        }
    }
    handlers["vy_group_question_submit"](lambda **kw: acks.append(kw), body, view)
    assert jobs[-1][0] == "group_question"
    for action in ["vy_group_config", "vy_group_auto_off", "vy_group_send", "vy_group_reject"]:
        before = len(jobs)
        data = {**body, "actions": [{"value": "CTEST"}]}
        if action == "vy_group_config":
            handlers[action](lambda: None, data, client)
        else:
            handlers[action](lambda: None, data)
        assert len(jobs) == before
    # Existing settings carry mode epoch; stale queued enables cannot undo OFF.
    owner = {**body, "user": {"id": "UOWNER"}, "actions": [{"value": "CTEST"}]}
    handlers["vy_group_config"](lambda: None, owner, client)
    assert (
        json.loads(views[-1]["private_metadata"])["auto_epoch"] == g.policy("CTEST")["auto_epoch"]
    )


def test_disallowed_source_not_in_model_prompt(group):
    g, _, _, prompts, _ = group
    configure(g)
    now = utcnow()
    g.c.backend.retrieval.upsert(
        {
            "session_id": "excluded-source",
            "source": "git",
            "redacted": True,
            "end_state": "EXCLUDED_SOURCE_FACT",
            "timestamp_range": {"started_at": now, "ended_at": now},
        }
    )
    g.c.backend.retrieval.assign_project(["excluded-source"], "virtualyou")
    asyncio.run(g.prepare(question(g)))
    assert "EXCLUDED_SOURCE_FACT" not in json.dumps(prompts)


def test_waiting_dispatch_worker_observes_automatic_off(group):
    g, _, calls, _, _ = group
    configure(g, True)
    original = g._dispatch

    def dispatch(value, client, automatic, actor):
        g.disable_auto("UOWNER", value["channel"])
        return original(value, client, automatic, actor)

    g._dispatch = dispatch
    key = question(g)
    asyncio.run(g.prepare(key))
    assert not calls and g.get(key)["state"] == "pending"


def test_owner_mention_routes_once_to_scoped_pipeline(group):
    g, slack, calls, prompts, model = group
    g.configure("UOWNER", "CCHAN", ["virtualyou"], ["claude"], "formal", automatic=True)
    event = dict(
        type="message",
        channel_type="channel",
        channel="CCHAN",
        user="UASKER",
        ts="100.1",
        text="<@UOWNER> What is the status?",
    )
    key = g.receive_mention(event, "TTEAM")
    assert key == g.receive_mention(event, "TTEAM")
    assert g.get(key)["question"] == "What is the status?"
    asyncio.run(g.prepare(key))
    assert g.get(key)["state"] == "sent"
    asyncio.run(g.prepare(key))
    assert len(calls) == 1
    assert calls[0]["thread_ts"] == "100.1"
    policy = g.policy("CCHAN")
    g.configure(
        "UOWNER",
        "CCHAN",
        ["virtualyou"],
        ["claude"],
        "formal",
        automatic=True,
        expected_revision=policy["revision"],
        expected_auto_epoch=policy["auto_epoch"],
    )
    assert g.receive_mention(event, "TTEAM") == key
    asyncio.run(g.prepare(key))
    assert len(calls) == 1


@pytest.mark.parametrize(
    "changes,team",
    [
        ({"text": "What is the status?"}, "TTEAM"),
        ({"text": "<@UOTHER> status?"}, "TTEAM"),
        ({"text": "<@UOWNER> <@UOTHER> status?"}, "TTEAM"),
        ({"text": "`<@UOWNER>` status?"}, "TTEAM"),
        ({"text": "> <@UOWNER> status?"}, "TTEAM"),
        ({"text": "<@UOWNER>"}, "TTEAM"),
        ({"user": "UOWNER"}, "TTEAM"),
        ({"bot_id": "BOTHER"}, "TTEAM"),
        ({"subtype": "message_changed"}, "TTEAM"),
        ({"channel_type": "im"}, "TTEAM"),
        ({"channel": "CDISABLED"}, "TTEAM"),
        ({}, "TOTHER"),
    ],
)
def test_mention_ignores_ambient_ambiguous_bot_and_private_messages(group, changes, team):
    g, *_ = group
    g.configure("UOWNER", "CCHAN", ["virtualyou"], ["claude"], "formal")
    event = dict(
        type="message",
        channel_type="channel",
        channel="CCHAN",
        user="UASKER",
        ts="100.1",
        text="<@UOWNER> status?",
    )
    event.update(changes)
    assert g.receive_mention(event, team) is None
    assert not g.requests()


def test_held_tagged_answer_posts_only_fixed_notice_once(group):
    g, slack, calls, prompts, model = group
    configure(g, automatic=True)
    model.eligible = False
    event = dict(
        channel_type="channel",
        channel="CTEST",
        user="UASKER",
        ts="100.1",
        text="<@UOWNER> What is the status?",
    )
    key = g.receive_mention(event, "TTEAM")
    asyncio.run(g.prepare(key))
    assert g.get(key)["state"] == "pending"
    assert g.get(key)["review_reason"] == "checked"
    assert g.get(key)["notice_state"] == "sent"
    assert len(calls) == 1
    assert "couldn't verify an answer" in calls[0]["text"]
    assert g.get(key)["result"]["text"] not in calls[0]["text"]
    g.review_notice(key)
    assert len(calls) == 1


def test_review_notice_respects_disabled_auto_and_membership(group):
    g, slack, calls, prompts, model = group
    configure(g, automatic=False)
    key = g.receive_mention(
        dict(
            channel_type="channel",
            channel="CTEST",
            user="UASKER",
            ts="100.1",
            text="<@UOWNER> status?",
        ),
        "TTEAM",
    )
    asyncio.run(g.prepare(key))
    assert not calls
    configure(g, automatic=True)
    with pytest.raises(ServiceError):
        g.review_notice(key)
    assert not calls


def test_malformed_automatic_review_is_explicit_failure(group):
    g, slack, calls, prompts, model = group
    configure(g, automatic=True)
    original = model.generate
    async def invalid(**kwargs):
        if kwargs['task'] == 'group_auto_review':
            return {'approved': True}
        return await original(**kwargs)
    model.generate = invalid
    key = question(g)
    asyncio.run(g.prepare(key))
    assert g.get(key)['reason'] == 'automatic_check_failed'
    assert not calls


def test_home_explains_review_and_notice(group):
    from virtualyou_workflow.group_views import group_blocks
    g, *_ = group
    configure(g)
    key = question(g)
    asyncio.run(g.prepare(key))
    value = g.get(key)
    value.update(review_reason='The PR is not identified.', notice_state='sent')
    g.save(value)
    rendered = json.dumps(group_blocks(g))
    assert 'The PR is not identified.' in rendered
    assert 'only a review-status notice was sent' in rendered
    assert 'nothing has been sent' not in rendered


def test_home_keeps_latest_group_questions_above_old_drafts(group):
    g, *_ = group
    configure(g)
    key = question(g)
    asyncio.run(g.prepare(key))
    blocks = g.c.home()['blocks']
    assert len(blocks) <= 100
    index = next(i for i, b in enumerate(blocks) if b.get('text', {}).get('text', '').startswith('Group question'))
    assert index < 15


def test_auto_review_repairs_once_and_rechecks_before_send(group):
    g, slack, calls, prompts, model = group
    configure(g, automatic=True)
    original = model.generate
    reviews = []
    feedback = []
    async def corrected(**kwargs):
        if kwargs['task'] == 'group_auto_review':
            reviews.append(True)
            return {'eligible': len(reviews) == 2, 'reason': 'Remove the unsupported test claim.' if len(reviews) == 1 else 'Supported by recorded changes.'}
        data = json.loads(kwargs['user'])
        feedback.append(data.get('validation_feedback'))
        return await original(**kwargs)
    model.generate = corrected
    key = question(g)
    asyncio.run(g.prepare(key))
    assert len(reviews) == 2
    assert any('unsupported test claim' in (f or '') for f in feedback)
    assert g.get(key)['review_revisions'] == 1
    assert g.get(key)['state'] == 'sent'
    assert len(calls) == 1


def test_repeated_review_rejection_stops_after_one_revision(group):
    g, slack, calls, prompts, model = group
    configure(g, automatic=True)
    model.eligible = False
    key = question(g)
    asyncio.run(g.prepare(key))
    assert sum(task == 'group_auto_review' for task, data in prompts) == 2
    assert g.get(key)['state'] == 'pending'
    assert not calls
