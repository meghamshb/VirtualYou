"""Explicit recipient learning from approved DM edits; never learn facts as style."""

import json

from virtual_you.backend.errors import ServiceError
from virtual_you.contracts.reporting import PersonaStyle
from virtual_you.ingest.redact import redact_text

from .formatting import validate_reply_disclosure
from .setup_views import modal
from .views import plain, section

KINDS = {
    "message": "This message only (including dates or commitments)",
    "style": "Phrasing / recipient style",
    "fact": "Factual correction / evidence flag",
    "audience": "Confidentiality / audience restriction",
}
STYLES = {
    "concise": "Use brief, direct sentences. Omit ceremonial completion language; preserve factual qualifiers and uncertainty.",
    "formal": "Use clear, complete professional sentences. Preserve factual qualifiers and uncertainty.",
    "bullets": "Use short bullets for multi-part updates. Preserve factual qualifiers and uncertainty.",
}


def validate_edit(text, kind):
    if kind not in KINDS or not isinstance(text, str) or not text.strip() or len(text) > 3000:
        raise ServiceError(
            "invalid_edit",
            "Enter a message of 1–3000 characters and choose a correction type.",
            422,
        )
    if redact_text(text) != text:
        raise ServiceError("unsafe_edit", "Remove secrets from the message before sending.", 422)
    return text.strip()


def learning_buttons(c, row):
    kind = row.get("edit_kind", "message")
    if kind == "fact":
        return [
            section(
                "Factual correction recorded for this reply. Its original evidence needs review; this edit does not establish a new verified fact or change style."
            )
        ]
    if kind == "audience":
        return [
            section(
                "Confidential details removed from this message. Review this person's allowed projects before future replies; no permanent rule was inferred."
            ),
            {
                "type": "actions",
                "elements": [button("Review audience policy", "vy_policy", row["recipient"])],
            },
        ]
    if kind != "style":
        return [section("This edit applies only to this message. No preference was learned.")]
    with c.backend.store.connection() as db:
        count = db.execute(
            "SELECT COUNT(*) FROM slack_dm_replies WHERE recipient=? AND state='sent' AND edit_kind='style' AND length(reply)<length(original_reply)*0.75",
            (row["recipient"],),
        ).fetchone()[0]
    return [
        section(
            "Repeated shorter edits detected. Consider a concise style for this person."
            if count >= 2
            else "Style is unchanged. Optionally choose a preference to remember for this person."
        ),
        {
            "type": "actions",
            "elements": [
                button("Remember this preference", "vy_learn", row["id"]),
                button("Style history / undo", "vy_style_history", row["id"]),
            ],
        },
    ]


def button(label, action, value):
    return {"type": "button", "text": plain(label), "action_id": action, "value": value}


def select(block, label, options, initial=None):
    values = [{"text": plain(title), "value": key} for key, title in options.items()]
    return {
        "type": "input",
        "block_id": block,
        "label": plain(label),
        "element": {
            "type": "static_select",
            "action_id": "value",
            "options": values,
            **(
                {"initial_option": next(x for x in values if x["value"] == initial)}
                if initial
                else {}
            ),
        },
    }


def register_learning(app, c, event_key):
    def row_for(body, reply_id):
        if not c.authorized(body) or not c.dm_replies:
            raise ServiceError("owner_only", "Only the configured owner can do this.", 403)
        return c.dm_replies.get(reply_id)

    @app.action("vy_dm_edit")
    def edit(ack, body, client):
        ack()
        if not c.authorized(body) or not c.dm_replies:
            return
        row = row_for(body, body["actions"][0]["value"])
        if row["state"] != "pending":
            return
        view = modal(
            "vy_dm_edit_submit",
            "Edit & send",
            [
                section(
                    "Submitting sends this exact edited message as you in the original DM. Keep the VirtualYou-assisted reply label if present. Nothing is learned automatically. For mixed edits, choose this message only and review style separately."
                ),
                {
                    "type": "input",
                    "block_id": "message",
                    "label": plain("Message"),
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "value",
                        "multiline": True,
                        "max_length": 3000,
                        "initial_value": row["reply"],
                    },
                },
                select("kind", "What did you correct?", KINDS, "message"),
            ],
            {"id": row["id"], "revision": row["edit_revision"]},
            submit="Send as me",
        )
        client.views_open(trigger_id=body["trigger_id"], view=view)

    @app.view("vy_dm_edit_submit")
    def edited(ack, body, view):
        try:
            data = json.loads(view["private_metadata"])
            row = row_for(body, data["id"])
            values = view["state"]["values"]
            kind = values["kind"]["value"]["selected_option"]["value"]
            text = validate_edit(values["message"]["value"]["value"], kind)
            validate_reply_disclosure(row, text)
            if row["state"] != "pending" or data["revision"] != row["edit_revision"]:
                raise ServiceError(
                    "reply_changed", "This draft is no longer pending. Reopen its card.", 409
                )
            c.state.enqueue(
                "dm_decision",
                {
                    "id": row["id"],
                    "approve": True,
                    "edited_text": text,
                    "edit_kind": kind,
                    "expected_revision": data["revision"],
                },
                event_key(body, "dm_edit"),
            )
        except (ServiceError, KeyError, ValueError) as error:
            ack(
                response_action="errors",
                errors={"message": getattr(error, "message", "Reopen the edit form.")},
            )
            return
        ack()

    def open_preference(history=False):
        def handler(ack, body, client):
            ack()
            if not c.authorized(body) or not c.dm_replies:
                return
            row = row_for(body, body["actions"][0]["value"])
            if row["state"] != "sent" or row.get("edit_kind") != "style":
                return
            profile_id = c.profile_id(row["recipient"])
            try:
                profile = c.backend.store.get_persona(profile_id)
            except ServiceError:
                client.views_open(
                    trigger_id=body["trigger_id"],
                    view={
                        "type": "modal",
                        "title": plain("Review style first"),
                        "close": plain("Close"),
                        "blocks": [
                            section(
                                "Select this person and review their style in VirtualYou Home before saving preferences. The delivered message is unchanged."
                            )
                        ],
                    },
                )
                return
            metadata = {"id": row["id"], "version": profile["version"]}
            if history:
                versions = c.backend.persona.history(profile_id)
                blocks = [
                    section(
                        "Undo restores the previous style as a new version. It never restores deleted examples or changes sent messages."
                    )
                ]
                blocks += [
                    section(
                        f"Version {v['version']}\n"
                        + json.dumps(v["style"], ensure_ascii=False)[:2500]
                    )
                    for v in versions[:20]
                ]
                view = modal(
                    "vy_style_undo", "Style history", blocks, metadata, submit="Undo latest"
                )
                if len(versions) < 2:
                    view.pop("submit")
            else:
                view = modal(
                    "vy_learn_submit",
                    "Remember preference",
                    [
                        section(
                            "Only the selected sentence style will change for this recipient. Facts, dates, examples, and audience permissions remain unchanged."
                        ),
                        section("Current sentence style: " + profile["style"]["sentence_style"]),
                        section(
                            "\n".join(f"{key.title()}: {value}" for key, value in STYLES.items())
                        ),
                        select(
                            "preference",
                            "Preference to remember",
                            {
                                "concise": "Brief and direct",
                                "formal": "Complete professional sentences",
                                "bullets": "Short bullets for multi-part updates",
                            },
                        ),
                    ],
                    metadata,
                )
            client.views_open(trigger_id=body["trigger_id"], view=view)

        return handler

    app.action("vy_learn")(open_preference())
    app.action("vy_style_history")(open_preference(True))

    def save_preference(undo=False):
        def handler(ack, body, view):
            try:
                data = json.loads(view["private_metadata"])
                row = row_for(body, data["id"])
                if row["state"] != "sent" or row.get("edit_kind") != "style":
                    raise ServiceError(
                        "invalid_learning", "Only a delivered style edit can be remembered.", 409
                    )
                profile_id = c.profile_id(row["recipient"])
                if undo:
                    profile = c.backend.persona.undo(profile_id, data["version"])
                else:
                    key = view["state"]["values"]["preference"]["value"]["selected_option"]["value"]
                    style = PersonaStyle.model_validate(
                        c.backend.store.get_persona(profile_id)["style"]
                    )
                    style.sentence_style = STYLES[key]
                    profile = c.backend.persona.revise(
                        profile_id, data["version"], style, remember_sentence_style=True
                    )
                person = c.state.recipient(row["recipient"])
                person.update(reviewed_version=profile.version, persona_version=profile.version)
                c.state.save_recipient(person)
            except (ServiceError, KeyError, ValueError) as error:
                ack(
                    response_action="update",
                    view={
                        "type": "modal",
                        "title": plain("Preference not saved"),
                        "close": plain("Close"),
                        "blocks": [
                            section(getattr(error, "message", "Reopen preference settings."))
                        ],
                    },
                )
                return
            ack(
                response_action="update",
                view={
                    "type": "modal",
                    "title": plain("Preference saved"),
                    "close": plain("Close"),
                    "blocks": [
                        section(
                            f"Recipient style is now version {profile.version}. Existing pending drafts require a fresh review. Use Style history / undo on the sent card to undo."
                        )
                    ],
                },
            )

        return handler

    app.view("vy_learn_submit")(save_preference())
    app.view("vy_style_undo")(save_preference(True))
