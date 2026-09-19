import asyncio

import pytest
from slack_bolt.error import BoltError
from slack_sdk.oauth.installation_store import Installation
from virtual_you.backend.errors import ServiceError

from virtualyou_workflow.oauth_store import OwnerInstallationStore
from virtualyou_workflow.setup_views import policy_modal, projects_modal, setup_modal, style_modal

from .test_workflow_coordinator import draft, selected


def test_onboarding_starts_without_source_or_project_access(setup):
    c, _, _ = setup
    assert c.preferences()["sources"] == []
    c.select("UFRIEND", True, "first")
    asyncio.run(c.process_once())
    value = c.state.recipient("UFRIEND")
    assert value["human_channel"] == "DHUMAN"
    with pytest.raises(ServiceError, match="Review"):
        c.require_ready(value)
    c.plan_automatic()
    assert c.state.claim() is None


def test_recipient_scope_filters_evidence_and_block_changes_before_sending(setup):
    c, _, network = setup
    selected(c)
    d = draft(c)
    c.backend.retrieval.assign_project(["new-work"], "Private project")
    c.state.enqueue("action", {"id": d["id"], "revision": d["revision"], "action": "approve"})
    asyncio.run(c.process_once())
    assert c.state.latest_error() == "audience_changed"
    assert c.backend.store.get_draft(d["id"])["status"] == "pending"
    assert not network
    assert c.backend.retrieval.search(c.scope(c.state.recipient("UFRIEND"))) == []


def test_source_revocation_blocks_existing_approval(setup):
    c, _, network = setup
    selected(c)
    d = draft(c)
    c.backend.store.set_metadata("slack_preferences", {"sources": [], "paused": False})
    with pytest.raises(ServiceError):
        c.require_current_policy(d["id"])
    assert not network


def test_style_review_removes_examples_and_requires_matching_version(setup):
    c, _, _ = setup
    selected(c)
    profile = c.backend.store.get_persona(c.profile_id("UFRIEND"))
    style = {**profile["style"], "tone": "Concise and friendly"}
    c.state.enqueue(
        "style_review",
        {
            "recipient": "UFRIEND",
            "version": profile["version"],
            "style": style,
            "remove_examples": True,
        },
    )
    asyncio.run(c.process_once())
    updated = c.backend.store.get_persona(c.profile_id("UFRIEND"))
    assert updated["examples"] == []
    assert "sample progress" not in updated["soul_md"]
    assert updated["style"]["tone"] == style["tone"]
    assert c.state.recipient("UFRIEND")["reviewed_version"] == updated["version"]
    c.state.enqueue(
        "style_review", {"recipient": "UFRIEND", "version": profile["version"], "style": style}
    )
    asyncio.run(c.process_once())
    assert c.state.latest_error() == "persona_conflict"


def test_new_forms_are_bounded_and_use_native_slack_inputs(setup):
    c, _, _ = setup
    selected(c)
    for view in [
        setup_modal(c),
        projects_modal(c),
        policy_modal(c, "UFRIEND"),
        style_modal(c, "UFRIEND"),
    ]:
        assert view["type"] == "modal"
        assert len(view["blocks"]) <= 100
        for block in view["blocks"]:
            element = block.get("element", {})
            if element.get("type") == "plain_text_input":
                assert element["max_length"] <= 3000
            if block["type"] == "section":
                assert len(block["text"]["text"]) <= 3000


def installation(**extra):
    return Installation(
        app_id="ATEST",
        team_id="TTEAM",
        user_id="UOWNER",
        bot_id="BTEST",
        bot_user_id="UBOT",
        bot_token="test-bot",
        user_token="test-user",
        user_scopes=["im:read", "im:history", "users:read"],
        **extra,
    )


def test_oauth_saves_tokens_automatically_and_restricts_owner(tmp_path):
    store = OwnerInstallationStore(base_dir=tmp_path, owner_id="UOWNER", team_id="TTEAM")
    value = installation()
    store.save(value)
    found = store.find_installation(
        enterprise_id=None, team_id="TTEAM", user_id="UOWNER", is_enterprise_install=False
    )
    assert found.user_token == "test-user"
    assert all(p.stat().st_mode & 0o077 == 0 for p in tmp_path.rglob("*") if p.is_file())
    value.user_id = "UOTHER"
    with pytest.raises(BoltError):
        store.save(value)
    assert (
        store.find_installation(
            enterprise_id=None, team_id="TTEAM", user_id="UOTHER", is_enterprise_install=False
        )
        is None
    )


def test_oauth_requires_history_scopes(tmp_path):
    store = OwnerInstallationStore(base_dir=tmp_path, owner_id="UOWNER", team_id="TTEAM")
    value = installation()
    value.user_scopes = ["users:read"]
    with pytest.raises(BoltError):
        store.save(value)
    assert not list(tmp_path.rglob("installer*"))


def test_actual_oauth_callback_exchanges_code_and_stores_without_token_pasting(tmp_path):
    from urllib.parse import parse_qs, urlparse

    from slack_bolt.oauth.oauth_flow import OAuthFlow
    from slack_bolt.oauth.oauth_settings import OAuthSettings
    from slack_bolt.request import BoltRequest
    from slack_sdk.oauth.state_store import FileOAuthStateStore

    store = OwnerInstallationStore(base_dir=tmp_path / "tokens", owner_id="UOWNER", team_id="TTEAM")
    settings = OAuthSettings(
        client_id="test-client",
        client_secret="test-secret",
        scopes=["chat:write"],
        user_scopes=["im:read", "im:history", "users:read"],
        redirect_uri="https://example.com/slack/oauth_redirect",
        install_page_rendering_enabled=False,
        installation_store=store,
        state_store=FileOAuthStateStore(expiration_seconds=600, base_dir=str(tmp_path / "states")),
    )
    flow = OAuthFlow(settings=settings)
    calls = []

    def exchange(code):
        calls.append(code)
        return installation()

    flow.run_installation = exchange
    start = flow.handle_installation(BoltRequest(body="", query="", headers={}))
    location = start.headers["location"][0]
    state = parse_qs(urlparse(location).query)["state"][0]
    cookie = start.headers["set-cookie"][0].split(";", 1)[0]
    result = flow.handle_callback(
        BoltRequest(
            body="", query={"code": "one-time-code", "state": state}, headers={"cookie": cookie}
        )
    )
    assert result.status == 200
    assert calls == ["one-time-code"]
    assert (
        store.find_installation(
            enterprise_id=None, team_id="TTEAM", user_id="UOWNER", is_enterprise_install=False
        ).user_token
        == "test-user"
    )
    flow.handle_callback(
        BoltRequest(body="", query={"code": "replayed", "state": state}, headers={"cookie": cookie})
    )
    assert calls == ["one-time-code"]


def test_reply_shortcut_requires_opt_in_and_matching_human_dm(setup):
    from types import SimpleNamespace

    from virtualyou_workflow.listeners import event_key
    from virtualyou_workflow.setup_listeners import register_setup

    class App:
        def __init__(self):
            self.handlers = {}

        def action(self, name):
            def save(fn):
                self.handlers[name] = fn
                return fn

            return save

        view = action
        shortcut = action

    c, _, network = setup
    selected(c)
    app = App()
    register_setup(app, c, event_key)
    views = []
    client = SimpleNamespace(views_open=lambda **kwargs: views.append(kwargs))
    body = {
        "user": {"id": "UOWNER"},
        "team": {"id": "TTEAM"},
        "channel": {"id": "DHUMAN"},
        "message": {"user": "UFRIEND", "text": "What changed? password=private-value", "ts": "2000000001.01"},
        "trigger_id": "one",
    }
    handler = app.handlers["vy_reply"]
    handler(lambda: None, body, client)
    assert c.state.claim() is None
    value = c.state.recipient("UFRIEND")
    value["reply_enabled"] = True
    c.state.save_recipient(value)
    body["channel"]["id"] = "DOTHER"
    handler(lambda: None, body, client)
    assert c.state.claim() is None
    body["channel"]["id"] = "DHUMAN"
    handler(lambda: None, body, client)
    job = c.state.claim()
    assert job["kind"] == "question" and job["payload"]["request"]["destination"]["send_as"] == "user"
    assert "private-value" not in job["payload"]["request"]["question"]
    assert not network
