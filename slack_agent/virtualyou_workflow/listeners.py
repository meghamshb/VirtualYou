import atexit
import hashlib
import json
import os
from urllib.parse import urljoin

from virtual_you.backend.errors import ServiceError
from virtual_you.ingest.redact import redact_text

from .config import Credentials, SlackSettings, private_installation_store
from .runtime import HeadlessRuntime
from .views import edit_modal, plain, reconcile_modal, selection_modal


def event_key(body, suffix=""):
    # Tokens/response URLs never enter durable jobs. Only hash the interaction's identity.
    identity = {key: body.get(key) for key in ("trigger_id", "event_id", "action_ts")}
    identity["view"] = body.get("view", {}).get("id")
    identity["actions"] = [
        {"action_id": a.get("action_id"), "action_ts": a.get("action_ts"), "value": a.get("value")}
        for a in body.get("actions", [])
    ]
    return hashlib.sha256((json.dumps(identity, sort_keys=True) + suffix).encode()).hexdigest()


def register(app, coordinator=None):
    if coordinator is None:
        config = SlackSettings.from_env()
        credentials = Credentials(config, private_installation_store())
        runtime = HeadlessRuntime(config, credentials)
        coordinator = runtime.start()
        atexit.register(runtime.stop)

    def is_owner(body):
        return coordinator.authorized(body)

    from .dm_replies import register_dm_actions
    register_dm_actions(app, coordinator, event_key)

    def publish(client, user):
        if user != coordinator.config.owner_id:
            client.views_publish(
                user_id=user,
                view={
                    "type": "home",
                    "blocks": [
                        {
                            "type": "section",
                            "text": plain(
                                "This VirtualYou installation belongs to its configured owner."
                            ),
                        }
                    ],
                },
            )
            return
        redirect = os.getenv("SLACK_REDIRECT_URI", "")
        install = urljoin(redirect, "/slack/install") if redirect else None
        client.views_publish(user_id=user, view=coordinator.home(install))

    @app.event("app_home_opened")
    def home(event, body, client, logger):
        if body.get("team_id") != coordinator.config.team_id:
            return
        if event.get("tab") == "home":
            publish(client, event["user"])

    @app.action("vy_choose")
    def choose(ack, body, client):
        ack()
        if is_owner(body):
            client.views_open(trigger_id=body["trigger_id"], view=selection_modal())

    @app.action("vy_connect")
    def connect(ack):
        ack()  # The button opens the configured OAuth installation URL.

    @app.view("vy_select_person")
    def select(ack, body, view):
        if not is_owner(body):
            ack(
                response_action="errors",
                errors={"recipient": "This installation is restricted to its owner."},
            )
            return
        values = view["state"]["values"]
        recipient = values["recipient"]["person"]["selected_user"]
        if recipient == coordinator.config.owner_id:
            ack(
                response_action="errors",
                errors={"recipient": "Select someone other than yourself."},
            )
            return
        automatic = bool(values.get("automatic", {}).get("enabled", {}).get("selected_options", []))
        # Only a small local transaction happens before acknowledging; model/history work is queued.
        coordinator.select(recipient, automatic, event_key(body, "select"))
        ack()

    @app.action("vy_refresh_home")
    def refresh_home(ack, body, client):
        ack()
        if is_owner(body):
            publish(client, coordinator.config.owner_id)

    @app.action("vy_toggle")
    def toggle(ack, body, client):
        ack()
        if is_owner(body):
            value = coordinator.state.recipient(body["actions"][0]["value"])
            coordinator.state.enqueue(
                "toggle",
                {"recipient": value["recipient"], "automatic": not value.get("automatic")},
                event_key(body, "toggle"),
            )

    def enqueue_person(kind, body):
        recipient = body["actions"][0]["value"]
        value = coordinator.state.recipient(recipient)
        if kind == "persona":
            value.update(status="preparing", error=None)
            coordinator.state.save_recipient(value)
        coordinator.state.enqueue(kind, {"recipient": recipient}, event_key(body, kind))

    @app.action("vy_style")
    def refresh_style(ack, body):
        ack()
        if is_owner(body):
            enqueue_person("persona", body)

    @app.action("vy_draft")
    def draft_now(ack, body):
        ack()
        if is_owner(body):
            enqueue_person("draft", body)

    def owned_draft(body):
        data = json.loads(body["actions"][0]["value"])
        coordinator.state.draft_link(data["id"])
        draft = coordinator.backend.store.get_draft(data["id"])
        if draft["revision"] != data["revision"]:
            raise ServiceError("revision_conflict", "Refresh the saved draft before editing.", 409)
        return draft

    @app.action("vy_edit")
    def edit(ack, body, client):
        ack()
        if is_owner(body):
            client.views_open(trigger_id=body["trigger_id"], view=edit_modal(owned_draft(body)))

    @app.action("vy_reconcile")
    def reconcile(ack, body, client):
        ack()
        if is_owner(body):
            client.views_open(
                trigger_id=body["trigger_id"], view=reconcile_modal(owned_draft(body))
            )

    @app.view("vy_edit_submit")
    def save_edit(ack, body, view):
        if not is_owner(body):
            ack(
                response_action="errors",
                errors={"message_0": "Only the configured owner may edit this draft."},
            )
            return
        data = json.loads(view["private_metadata"])
        coordinator.state.draft_link(data["id"])
        parts = int(data.pop("parts", 1))
        if not 1 <= parts <= 4:
            ack(response_action="errors", errors={"message_0": "Invalid update length."})
            return
        text = redact_text(
            "".join(
                view["state"]["values"][f"message_{i}"]["text"].get("value") or ""
                for i in range(parts)
            )
        ).strip()
        if not text:
            ack(response_action="errors", errors={"message_0": "Enter an update before saving."})
            return
        coordinator.state.enqueue(
            "action", {**data, "action": "edit", "text": text}, event_key(body, "edit")
        )
        ack()

    @app.view("vy_reconcile_submit")
    def save_reconciliation(ack, body, view):
        if not is_owner(body):
            ack(
                response_action="errors",
                errors={"outcome": "Only the configured owner can resolve delivery."},
            )
            return
        data = json.loads(view["private_metadata"])
        coordinator.state.draft_link(data["id"])
        values = view["state"]["values"]
        outcome = values["outcome"]["value"]["selected_option"]["value"]
        message_id = values["message_id"]["value"].get("value") or None
        if outcome == "confirmed_delivered" and not message_id:
            ack(
                response_action="errors",
                errors={"message_id": "Enter the verified Slack message timestamp."},
            )
            return
        coordinator.state.enqueue(
            "action",
            {
                **data,
                "action": "reconcile",
                "outcome": outcome,
                "message_id": message_id,
                "note": redact_text(values["note"]["value"]["value"]),
            },
            event_key(body, "reconcile"),
        )
        ack()

    def action_handler(action):
        def handle(ack, body):
            ack()
            if is_owner(body):
                data = json.loads(body["actions"][0]["value"])
                coordinator.state.draft_link(data["id"])
                coordinator.state.enqueue(
                    "action", {**data, "action": action}, event_key(body, action)
                )

        return handle

    for action in ("approve", "deliver", "regenerate", "reject"):
        app.action("vy_" + action)(action_handler(action))

    @app.event("message")
    def owner_message(event, body, client):
        if coordinator.dm_replies:
            coordinator.dm_replies.receive_event(event, body.get("team_id"))
        if getattr(coordinator.dm_replies, "all_personal_dms", False):
            return  # Home is the control surface; never echo into personal DMs.
        # User-authorized events include human-to-human DMs. Never post the
        # bot onboarding response into those conversations or echo own replies.
        if not any(a.get("is_bot") for a in body.get("authorizations", [])):
            return
        if any(event.get("channel") == p.get("human_channel") for p in coordinator.state.recipients()):
            return
        if event.get("subtype") or event.get("bot_id") or event.get("channel_type") != "im":
            return
        if (
            event.get("user") != coordinator.config.owner_id
            or body.get("team_id") != coordinator.config.team_id
        ):
            return
        # Owner commands expose the same controls; manager DMs never invoke unrestricted generation.
        client.chat_postMessage(
            channel=event["channel"],
            text="Open VirtualYou Home to choose a person and review your updates.",
            blocks=[
                {
                    "type": "section",
                    "text": plain(
                        "Choose a person to prepare updates for. Existing drafts are available in VirtualYou Home."
                    ),
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": plain("Choose a person"),
                            "action_id": "vy_choose",
                            "value": "choose",
                        }
                    ],
                },
            ],
        )

    # Explicitly acknowledge unsupported mentions without publishing a response as the owner.
    @app.event("app_mention")
    def mention(event):
        return

    from .setup_listeners import register_setup

    register_setup(app, coordinator, event_key)
    return coordinator
