import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from virtual_you.backend.app import create_app
from virtual_you.backend.config import Settings
from virtual_you.contracts.reporting import utcnow

from virtualyou_workflow.config import SlackSettings
from virtualyou_workflow.coordinator import Coordinator
from virtualyou_workflow.state import SlackState
from virtualyou_workflow.views import draft_blocks, edit_modal, selection_modal


class FakeSlack:
    def __init__(self, calls, token):
        self.calls, self.token = calls, token

    def auth_test(self):
        return {"team_id": "TTEAM", "user_id": "UOWNER" if self.token == "user-token" else "UBOT"}

    def conversations_list(self, **kwargs):
        return {"channels": [{"id": "DHUMAN", "is_im": True, "user": "UFRIEND"}]}

    def conversations_history(self, **kwargs):
        self.calls.append(("history", kwargs))
        return {
            "messages": [
                {
                    "user": "UOWNER",
                    "text": f"Hey, sample progress update {i}. Cheers.",
                    "ts": f"{100 + i}.0",
                }
                for i in range(20)
            ]
        }

    def users_info(self, **kwargs):
        return {"user": {"id": "UFRIEND", "profile": {"display_name": "My colleague"}}}

    def conversations_open(self, **kwargs):
        self.calls.append(("open", kwargs))
        return {"channel": {"id": "DOWNER" if kwargs["users"] == "UOWNER" else "DBOTFRIEND"}}

    def chat_postMessage(self, **kwargs):
        self.calls.append(("post", kwargs))
        return {"ts": "1000.01"}

    def chat_update(self, **kwargs):
        self.calls.append(("update", kwargs))
        return {"ts": "1000.01"}

    def views_publish(self, **kwargs):
        self.calls.append(("home", kwargs))


@pytest.fixture
def setup(tmp_path):
    settings = Settings(data_dir=tmp_path, heartbeat_enabled=False)
    network = []

    def transport(request):
        network.append(request)
        return httpx.Response(200, json={"ok": True, "ts": "2000.02"})

    with TestClient(create_app(settings, transport=httpx.MockTransport(transport))) as client:
        calls = []
        credentials = SimpleNamespace(
            user_token=lambda: "user-token", bot_token=lambda: "bot-token", connected=lambda: True
        )
        config = SlackSettings(
            "UOWNER", "TTEAM", quiet_seconds=0.001, minimum_interval_seconds=0.001
        )
        coordinator = Coordinator(
            client.app.state,
            config,
            credentials,
            client_factory=lambda token, **kwargs: FakeSlack(calls, token),
        )
        now = utcnow()
        client.app.state.retrieval.upsert(
            {
                "session_id": "new-work",
                "source": "claude",
                "redacted": True,
                "start_state": "Add validation",
                "end_state": "Validation added and tested.",
                "timestamp_range": {"started_at": now, "ended_at": now},
            }
        )
        yield coordinator, calls, network


def selected(coordinator):
    coordinator.select("UFRIEND", True, "initial-selection")
    asyncio.run(coordinator.process_once())
    assert coordinator.state.recipient("UFRIEND")["status"] == "ready"
    coordinator.backend.retrieval.assign_project(["new-work"], "Project A")
    coordinator.backend.store.set_metadata(
        "slack_preferences", {"sources": ["claude"], "paused": False}
    )
    value = coordinator.state.recipient("UFRIEND")
    value.update(
        projects=["Project A"],
        reviewed_version=coordinator.backend.store.get_persona(coordinator.profile_id("UFRIEND"))[
            "version"
        ],
    )
    coordinator.state.save_recipient(value)


def draft(coordinator):
    coordinator.state.enqueue("draft", {"recipient": "UFRIEND"})
    asyncio.run(coordinator.process_once())
    return coordinator.state.cards()[0]


def test_select_builds_persona_from_history_and_saves_selection(setup):
    c, calls, network = setup
    selected(c)
    profile = c.backend.store.get_persona(c.profile_id("UFRIEND"))
    assert len(profile["examples"]) == 5
    assert c.state.recipient("UFRIEND")["sample_count"] == 20
    assert c.state.recipient("UFRIEND")["bot_channel"] == "DBOTFRIEND"
    assert "DBOTFRIEND" in c.backend.settings.slack_channels
    assert not [x for x in calls if x[0] == "post"]
    with c.backend.store.connection() as db:
        jobs = db.execute("SELECT payload FROM slack_jobs").fetchall()
    assert "sample progress" not in str([r[0] for r in jobs])


def test_draft_notifies_only_owner_and_approval_goes_through_backend(setup):
    c, calls, network = setup
    selected(c)
    d = draft(c)
    posts = [args for name, args in calls if name == "post"]
    assert len(posts) == 1 and posts[0]["channel"] == "DOWNER"
    assert d["status"] == "pending" and not network
    c.state.enqueue("action", {"id": d["id"], "revision": 1, "action": "approve"})
    asyncio.run(c.process_once())
    assert c.backend.store.get_draft(d["id"])["status"] == "simulated"
    assert not network


def test_live_approval_sends_bot_message_to_selected_person_once(setup):
    c, calls, network = setup
    selected(c)
    c.backend.settings.live_delivery = True
    d = draft(c)
    for _ in range(2):
        c.state.enqueue("action", {"id": d["id"], "revision": 1, "action": "approve"})
        asyncio.run(c.process_once())
    assert len(network) == 1
    assert json.loads(network[0].content)["channel"] == "DBOTFRIEND"
    assert c.backend.store.get_draft(d["id"])["status"] == "delivered"


def test_automatic_queue_is_debounced_and_does_not_duplicate_pending_draft(setup):
    c, calls, network = setup
    selected(c)
    import time

    time.sleep(0.01)
    c.plan_automatic()
    c.plan_automatic()
    asyncio.run(c.process_once())
    assert len(c.state.cards()) == 1
    c.plan_automatic()
    assert c.state.claim() is None
    assert not network


def test_pause_blocks_automatic_drafting(setup):
    c, calls, network = setup
    selected(c)
    value = c.state.recipient("UFRIEND")
    value["automatic"] = False
    c.state.save_recipient(value)
    c.plan_automatic()
    assert c.state.claim() is None


def test_owner_workspace_and_draft_binding_are_enforced(setup):
    c, _, _ = setup
    assert c.authorized({"user": {"id": "UOWNER"}, "team": {"id": "TTEAM"}})
    assert not c.authorized({"user": {"id": "UFRIEND"}, "team": {"id": "TTEAM"}})
    assert not c.authorized({"user": {"id": "UOWNER"}, "team": {"id": "TOTHER"}})
    from virtual_you.backend.errors import ServiceError

    with pytest.raises(ServiceError):
        c.state.draft_link("arbitrary-backend-draft")


def test_restart_preserves_selection_and_never_replays_delivery_action(setup):
    c, _, _ = setup
    selected(c)
    c.state.enqueue("action", {"id": "test", "revision": 1, "action": "approve"})
    job = c.state.claim()
    assert job["kind"] == "action"
    restarted = SlackState(c.backend.store)
    assert restarted.recipient("UFRIEND")["status"] == "ready"
    assert restarted.claim() is None
    assert restarted.latest_error() == "interrupted_action"


def test_slack_views_show_full_text_and_native_edit_modal(setup):
    c, _, _ = setup
    selected(c)
    d = draft(c)
    blocks = draft_blocks(d, "Friend", False)
    assert d["text"] in json.dumps(blocks).replace("\\n", "\n")
    ids = [b["action_id"] for row in blocks if row["type"] == "actions" for b in row["elements"]]
    assert set(ids) == {"vy_approve", "vy_edit", "vy_regenerate", "vy_reject"}
    modal = edit_modal(d)
    assert modal["type"] == "modal" and modal["blocks"][1]["element"]["initial_value"] == d["text"]
    assert selection_modal()["blocks"][1]["element"]["type"] == "users_select"
    assert c.home()["type"] == "home"


def test_long_edit_modal_preserves_text_within_slack_limits():
    text = "a" * 12000
    modal = edit_modal({"id": "test", "revision": 1, "text": text})
    inputs = modal["blocks"][1:]
    assert len(inputs) == 4
    assert "".join(row["element"]["initial_value"] for row in inputs) == text
    assert all(row["element"]["max_length"] <= 3000 for row in inputs)


def test_listener_rejects_nonowner_and_reassembles_long_edits(setup):
    from virtualyou_workflow.listeners import register

    class App:
        def __init__(self):
            self.handlers = {}

        def action(self, name):
            return self.event(name)

        def shortcut(self, name):
            return self.event(name)

        def view(self, name):
            return self.event(name)

        def event(self, name):
            def save(fn):
                self.handlers[name] = fn
                return fn

            return save

    c, _, _ = setup
    selected(c)
    d = draft(c)
    app = App()
    register(app, c)
    body = {
        "user": {"id": "UFRIEND"},
        "team": {"id": "TTEAM"},
        "trigger_id": "unique",
        "actions": [{"value": json.dumps({"id": d["id"], "revision": 1})}],
    }
    acks = []
    app.handlers["vy_approve"](lambda **kw: acks.append(kw), body)
    assert acks == [{}]
    assert c.state.claim() is None
    body["user"]["id"] = "UOWNER"
    text = "a" * 3000 + "\nSecond part"
    view = {
        "private_metadata": json.dumps({"id": d["id"], "revision": 1, "parts": 2}),
        "state": {
            "values": {
                "message_0": {"text": {"value": text[:3000]}},
                "message_1": {"text": {"value": text[3000:]}},
            }
        },
    }
    app.handlers["vy_edit_submit"](lambda **kw: acks.append(kw), body, view)
    job = c.state.claim()
    assert job["payload"]["text"] == text
    assert job["payload"]["action"] == "edit"


def test_runtime_starts_and_stops_without_http_server(tmp_path, monkeypatch):
    from virtualyou_workflow.runtime import HeadlessRuntime

    monkeypatch.setenv("VIRTUAL_YOU_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VIRTUAL_YOU_LLM_PROVIDER", "demo")
    credentials = SimpleNamespace(
        bot_token=lambda: "", user_token=lambda: "", connected=lambda: False
    )
    runtime = HeadlessRuntime(SlackSettings("UOWNER", "TTEAM"), credentials)
    try:
        coordinator = runtime.start()
        assert coordinator.config.owner_id == "UOWNER"
        assert runtime.thread.is_alive()
    finally:
        runtime.stop()
    assert not runtime.thread.is_alive()
    assert runtime.error is None
