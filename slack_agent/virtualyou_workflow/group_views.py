"""Slack group controls for owner mentions and shortcuts; owner-only policy changes."""

import json

from virtual_you.backend.errors import ServiceError

from .formatting import formatted_section
from .groups import GROUP_STYLES, SOURCES
from .learning import button, select
from .setup_views import choices, modal, multi_select, text_input
from .views import plain, section


def group_reason(reason):
    if reason == "missing_evidence":
        return (
            "No supported project answer was found. Use Ask a VirtualYou again with "
            "a specific question, such as: What changed in the VirtualYou ingestion pipeline? "
            "A greeting such as 'Yo' does not identify work to report. No reply was sent."
        )
    return {
        "automatic_review_required": "Held for owner review; see the draft and evidence below.",
        "automatic_check_failed": "Automatic validation failed. No project answer was sent.",
        "invalid_citation": "The model returned an invalid source reference. No project answer was sent.",
    }.get(reason, reason)


def group_blocks(groups):
    blocks = [
        section(
            "Group conversations: a colleague tags the owner with a question, or uses Ask a VirtualYou. "
            "Each conversation has its own audience scope. "
            + ("Live replies appear in the original thread as the owner, labelled VirtualYou. "
               "Approval is required unless the owner explicitly enabled automatic replies for that conversation."
               if groups.live_delivery else
               "Simulation mode: group replies are prepared, but even an approved reply sends nothing to the channel.")
        ),
        {
            "type": "actions",
            "elements": [button("Enable a conversation", "vy_group_config", "new")],
        },
    ]
    for policy in groups.policies()[:8]:
        mode = "AUTOMATIC" if policy["automatic"] else "OWNER APPROVAL"
        blocks.extend(
            [
                section(
                    f"{policy['channel']} · {mode} · {'enabled' if policy['enabled'] else 'disabled'}\nProjects: {', '.join(policy['projects'])}. Sources: {', '.join(policy['sources'])}. Group style: {policy['style']}."
                ),
                {
                    "type": "actions",
                    "elements": [
                        button("Conversation settings", "vy_group_config", policy["channel"]),
                        button(
                            "Turn off automatic replies", "vy_group_auto_off", policy["channel"]
                        ),
                    ],
                },
            ]
        )
    for request in groups.requests()[:8]:
        blocks.append(
            section(
                f"Group question in {request['channel']} from {request['requester']} · {request['state']}\n{request['question']}\n{group_reason(request.get('reason', ''))}"
                + (
                    ("\nAwaiting owner approval; only a review-status notice was sent." if request.get("notice_state") == "sent" else "\nAwaiting owner approval; no answer has been sent.")
                    if request["state"] == "pending"
                    else ""
                )
            )
        )
        if request.get("review_reason"):
            blocks.append(section("Automatic review: " + request["review_reason"]))
        if request["state"] == "pending":
            blocks.append(formatted_section(request["result"]["text"][:2500]))
            quotes = [
                c["quote"]
                for p in request["result"].get("paragraphs", [])
                for c in p.get("citations", [])
                if c.get("quote")
            ]
            if quotes:
                blocks.append(
                    section("Supporting evidence (review claims):\n" + "\n".join(quotes)[:2000])
                )
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        button("Approve group reply" if groups.live_delivery else "Approve simulation", "vy_group_send", request["id"]),
                        button("Reject", "vy_group_reject", request["id"]),
                    ],
                }
            )
    return blocks


def register_groups(app, c, event_key):
    def feedback(client, body, text):
        client.views_open(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "title": plain("Group assistance"),
                "close": plain("Close"),
                "blocks": [section(text)],
            },
        )

    @app.shortcut("vy_group_ask")
    def ask(ack, body, client):
        ack()
        try:
            intent = c.groups.intent(body)
        except ServiceError as error:
            feedback(client, body, error.message)
            return
        view = modal(
            "vy_group_question_submit",
            "Ask a VirtualYou",
            [
                section(
                    f"This installation serves {c.config.owner_id}. Your question uses only this conversation's approved projects and group style. Responses are labelled VirtualYou and posted as that owner in this thread."
                ),
                {
                    "type": "input",
                    "block_id": "owner",
                    "label": plain("Whose VirtualYou?"),
                    "element": {
                        "type": "users_select",
                        "action_id": "value",
                        "initial_user": c.config.owner_id,
                    },
                },
                text_input("question", "Question", intent["question"], maximum=1000),
            ],
            {"intent": intent["id"]},
            submit="Ask",
        )
        client.views_open(trigger_id=body["trigger_id"], view=view)

    @app.view("vy_group_question_submit")
    def question(ack, body, view):
        try:
            if body.get("team", {}).get("id") != c.config.team_id:
                raise ServiceError("wrong_team", "Use the configured workspace.", 403)
            values = view["state"]["values"]
            c.groups.enqueue(
                json.loads(view["private_metadata"])["intent"],
                body["user"]["id"],
                values["owner"]["value"]["selected_user"],
                values["question"]["value"]["value"],
            )
        except (ServiceError, KeyError, ValueError) as error:
            ack(
                response_action="errors",
                errors={"question": getattr(error, "message", "Reopen the shortcut.")},
            )
            return
        ack(
            response_action="update",
            view={
                "type": "modal",
                "title": plain("Question queued"),
                "close": plain("Close"),
                "blocks": [
                    section(
                        "Your explicitly requested question is queued. Replies default to owner approval; the owner can review status in VirtualYou Home. No message has been sent yet."
                    )
                ],
            },
        )

    @app.action("vy_group_config")
    def config(ack, body, client):
        ack()
        if not c.authorized(body):
            return
        channel = body["actions"][0]["value"]
        policy = c.groups.policy(channel) if channel != "new" else None
        blocks = [
            section(
                "Review the audience before saving. Public channel scopes apply to everyone who can read that channel. Shared/external conversations are blocked. Private-DM permissions and personas are never inherited."
            )
        ]
        if policy:
            blocks.append(section("Conversation: " + channel))
        else:
            blocks.append(
                {
                    "type": "input",
                    "block_id": "conversation",
                    "label": plain("Conversation"),
                    "element": {
                        "type": "conversations_select",
                        "action_id": "value",
                        "filter": {
                            "include": ["public", "private", "mpim"],
                            "exclude_external_shared_channels": True,
                        },
                    },
                }
            )
        projects = c.backend.retrieval.project_choices()
        if not projects:
            feedback(client, body, "Index a project before configuring group assistance.")
            return
        blocks.extend(
            [
                multi_select(
                    "projects",
                    "Allowed projects",
                    [(p, p) for p in projects[:100]],
                    policy["projects"] if policy else [],
                ),
                multi_select(
                    "sources",
                    "Allowed work sources",
                    [(s, s) for s in sorted(SOURCES)],
                    policy["sources"] if policy else [],
                ),
                select(
                    "style",
                    "Reviewed group style",
                    {"formal": "Formal group updates", "bullets": "Concise factual bullets"},
                    policy["style"] if policy else "formal",
                ),
                section("\n".join(f"{k}: {v}" for k, v in GROUP_STYLES.items())),
                choices(
                    "enabled",
                    "Activation",
                    [("Enable explicit questions in this conversation", "yes")],
                    ["yes"] if policy and policy["enabled"] else [],
                ),
                choices(
                    "automatic",
                    "Sending mode",
                    [("Allow automatic replies as me (labelled VirtualYou)", "yes")],
                    ["yes"] if policy and policy["automatic"] else [],
                ),
                choices(
                    "reviewed",
                    "Audience and style review",
                    [("I reviewed this group scope and group style", "yes")],
                ),
            ]
        )
        client.views_open(
            trigger_id=body["trigger_id"],
            view=modal(
                "vy_group_config_submit",
                "Conversation settings",
                blocks,
                {
                    "channel": channel,
                    "revision": policy["revision"] if policy else None,
                    "auto_epoch": policy["auto_epoch"] if policy else None,
                },
            ),
        )

    @app.view("vy_group_config_submit")
    def configured(ack, body, view):
        if not c.authorized(body):
            ack(
                response_action="errors",
                errors={"reviewed": "Only the configured owner can change group settings."},
            )
            return
        values = view["state"]["values"]

        def selected(key):
            return [o["value"] for o in values[key]["value"].get("selected_options", [])]

        try:
            if not selected("reviewed") or not selected("projects") or not selected("sources"):
                raise ValueError()
            data = json.loads(view["private_metadata"])
            channel = (
                data["channel"]
                if data["channel"] != "new"
                else values["conversation"]["value"]["selected_conversation"]
            )
            payload = dict(
                channel=channel,
                projects=selected("projects"),
                sources=selected("sources"),
                style=values["style"]["value"]["selected_option"]["value"],
                automatic=bool(selected("automatic")),
                enabled=bool(selected("enabled")),
                expected_revision=data["revision"],
                expected_auto_epoch=data["auto_epoch"],
            )
            # An OFF selection takes effect now, before queued membership verification.
            if not payload["automatic"]:
                c.groups.disable_auto(c.config.owner_id, channel)
                current = c.groups.policy(channel)
                payload["expected_auto_epoch"] = current["auto_epoch"] if current else None
            c.state.enqueue("group_configure", payload, event_key(body, "group_configure"))
        except (ValueError, KeyError):
            ack(
                response_action="errors",
                errors={
                    "reviewed": "Select projects and sources, and confirm the audience/style review."
                },
            )
            return
        ack()

    @app.action("vy_group_auto_off")
    def off(ack, body):
        if c.authorized(body):
            c.groups.disable_auto(c.config.owner_id, body["actions"][0]["value"])
        ack()
        if c.authorized(body):
            c.publish_home()

    @app.action("vy_group_send")
    def send(ack, body):
        ack()
        if c.authorized(body):
            c.state.enqueue(
                "group_send", {"id": body["actions"][0]["value"]}, event_key(body, "group_send")
            )

    @app.action("vy_group_reject")
    def reject(ack, body):
        ack()
        if c.authorized(body):
            with c.groups.lock, c.backend.store.connection(write=True) as db:
                key = body["actions"][0]["value"]
                row = db.execute("SELECT payload FROM group_requests WHERE id=?", (key,)).fetchone()
                if row:
                    value = json.loads(row[0])
                    if value["state"] == "pending":
                        value["state"] = "rejected"
                        db.execute(
                            "UPDATE group_requests SET payload=? WHERE id=?",
                            (json.dumps(value), key),
                        )
                        c.groups.audit(
                            db, "owner_rejected", {"id": key, "owner": c.config.owner_id}
                        )
            c.publish_home()
