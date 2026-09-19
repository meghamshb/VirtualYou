import json

from pydantic import ValidationError
from virtual_you.backend.errors import ServiceError
from virtual_you.contracts.reporting import PersonaStyle
from virtual_you.ingest.redact import redact_text

from .setup_views import (
    modal,
    policy_modal,
    projects_modal,
    session_option,
    setup_modal,
    style_modal,
)
from .views import section


def register_setup(app, coordinator, event_key):
    def owner(body):
        return coordinator.authorized(body)

    def open_handler(builder, recipient=False):
        def handle(ack, body, client):
            ack()
            if not owner(body):
                return
            try:
                view = (
                    builder(coordinator, body["actions"][0]["value"])
                    if recipient
                    else builder(coordinator)
                )
            except ServiceError as error:
                view = {
                    "type": "modal",
                    "title": {"type": "plain_text", "text": "Not ready yet"},
                    "close": {"type": "plain_text", "text": "Close"},
                    "blocks": [section(error.message)],
                }
            client.views_open(trigger_id=body["trigger_id"], view=view)

        return handle

    for name, builder, recipient in [
        ("setup", setup_modal, False),
        ("projects", projects_modal, False),
        ("policy", policy_modal, True),
        ("review_style", style_modal, True),
    ]:
        app.action("vy_" + name)(open_handler(builder, recipient))

    @app.action("vy_check_connection")
    def check_connection(ack, body):
        ack()
        if owner(body):
            coordinator.state.enqueue("check_connection", {}, event_key(body, "check_connection"))

    def submit(kind, error_block):
        def handle(ack, body, view):
            if not owner(body):
                ack(
                    response_action="errors",
                    errors={error_block: "Only the configured owner can change these settings."},
                )
                return
            values = view["state"]["values"]

            def text(key):
                return redact_text(values.get(key, {}).get("value", {}).get("value") or "").strip()

            def selected(key):
                return [
                    o["value"]
                    for o in values.get(key, {}).get("value", {}).get("selected_options", [])
                ]

            data = json.loads(view.get("private_metadata") or "{}")
            try:
                if kind == "preferences":
                    data = {"sources": selected("sources"), "paused": bool(selected("paused"))}
                elif kind == "projects":
                    lookup = {
                        session_option(r)[1]: r["session_id"]
                        for r in coordinator.backend.retrieval.activity_choices()
                    }
                    ids = selected("sessions")
                    if not ids or any(i not in lookup for i in ids):
                        raise ValueError("Choose available activities.")
                    data = {"project": text("project"), "sessions": [lookup[i] for i in ids]}
                elif kind == "policy":
                    coordinator.state.recipient(data["recipient"])
                    interval = int(text("interval"))
                    if not 1 <= interval <= 10080:
                        raise ValueError("Cadence must be 1–10080 minutes.")
                    data.update(
                        projects=selected("projects"),
                        purpose=text("purpose"),
                        interval=interval,
                        reply_enabled=bool(selected("reply")),
                    )
                elif kind == "style_review":
                    coordinator.state.recipient(data["recipient"])
                    style = {
                        k: text(k)
                        for k in [
                            "tone",
                            "greeting",
                            "sign_off",
                            "sentence_style",
                            "punctuation",
                            "emoji",
                        ]
                    }
                    style.update(
                        formality=values["formality"]["value"]["selected_option"]["value"],
                        vocabulary=[v.strip() for v in text("vocabulary").split(",") if v.strip()],
                    )
                    data.update(
                        style=PersonaStyle.model_validate(style).model_dump(),
                        remove_examples=bool(selected("remove")),
                    )
                coordinator.state.enqueue(kind, data, event_key(body, kind))
            except (ValueError, KeyError, ServiceError, ValidationError):
                ack(
                    response_action="errors",
                    errors={
                        error_block: "Check these settings and reopen the form if the data changed."
                    },
                )
                return
            ack()

        return handle

    for callback, kind, field in [
        ("setup", "preferences", "sources"),
        ("projects", "projects", "sessions"),
        ("policy", "policy", "interval"),
        ("style_review", "style_review", "tone"),
    ]:
        app.view("vy_" + callback + "_submit")(submit(kind, field))

    @app.shortcut("vy_reply")
    def reply(ack, body, client):
        ack()
        if not owner(body):
            return
        try:
            message = body["message"]
            value = coordinator.state.recipient(message["user"])
            coordinator.require_ready(value)
            if (
                not value.get("reply_enabled")
                or value.get("human_channel") != body["channel"]["id"]
            ):
                raise ServiceError(
                    "reply_disabled",
                    "Enable reply assistance in this person's settings and use their one-to-one DM.",
                )
            if (
                message.get("bot_id")
                or message.get("subtype")
                or not message.get("text", "").strip()
            ):
                raise ServiceError(
                    "unsupported_message", "Choose a text message written by this colleague."
                )
            question = redact_text(message["text"])[:1000]
            identity = body["channel"]["id"] + ":" + message.get("ts", event_key(body, "reply"))
            coordinator.queue_question(value["recipient"], question, identity)
            explanation = "This request will be checked against allowed, recent evidence. Review the draft or escalation in VirtualYou Home. No answer is sent without your approval."
        except (KeyError, ServiceError) as error:
            explanation = (
                error.message
                if isinstance(error, ServiceError)
                else "Choose a supported message in a selected colleague's DM."
            )
        view = modal("vy_reply_notice", "Draft for this request", [section(explanation)])
        view.pop("submit")
        client.views_open(trigger_id=body["trigger_id"], view=view)
