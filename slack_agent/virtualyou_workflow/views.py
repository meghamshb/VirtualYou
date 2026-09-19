import json


def plain(text):
    return {"type": "plain_text", "text": str(text), "emoji": True}


def section(text):
    return {"type": "section", "text": plain(text[:3000])}


def button(label, action, value="", style=None):
    result = {"type": "button", "text": plain(label), "action_id": action, "value": value or action}
    if style:
        result["style"] = style
    return result


def selection_modal():
    return {
        "type": "modal",
        "callback_id": "vy_select_person",
        "title": plain("Choose a person"),
        "submit": plain("Create their style"),
        "close": plain("Cancel"),
        "blocks": [
            section(
                "VirtualYou will read up to 20 recent messages written by you in your existing DM with this person. Their messages are excluded. Reports still require your approval."
            ),
            {
                "type": "input",
                "block_id": "recipient",
                "label": plain("Person"),
                "element": {
                    "type": "users_select",
                    "action_id": "person",
                    "placeholder": plain("Select a person"),
                },
            },
            {
                "type": "input",
                "block_id": "automatic",
                "label": plain("Prepare updates"),
                "optional": True,
                "element": {
                    "type": "checkboxes",
                    "action_id": "enabled",
                    "options": [
                        {
                            "text": plain(
                                "Prepare drafts automatically when new work is available"
                            ),
                            "value": "yes",
                        }
                    ],
                    "initial_options": [
                        {
                            "text": plain(
                                "Prepare drafts automatically when new work is available"
                            ),
                            "value": "yes",
                        }
                    ],
                },
            },
        ],
    }


def edit_modal(draft):
    chunks = [draft["text"][i : i + 3000] for i in range(0, len(draft["text"]), 3000)] or [""]
    return {
        "type": "modal",
        "callback_id": "vy_edit_submit",
        "title": plain("Edit update"),
        "submit": plain("Save draft"),
        "close": plain("Cancel"),
        "private_metadata": json.dumps(
            {"id": draft["id"], "revision": draft["revision"], "parts": len(chunks)}
        ),
        "blocks": [
            section(
                "Saving requires fresh approval. Long updates are split into consecutive parts; parts join exactly as entered."
            )
        ]
        + [
            {
                "type": "input",
                "block_id": f"message_{i}",
                "optional": True,
                "label": plain("Your update" if len(chunks) == 1 else f"Your update, part {i + 1}"),
                "element": {
                    "type": "plain_text_input",
                    "action_id": "text",
                    "multiline": True,
                    "max_length": 3000,
                    **({"initial_value": chunk} if chunk else {}),
                },
            }
            for i, chunk in enumerate(chunks)
        ],
    }


def reconcile_modal(draft):
    return {
        "type": "modal",
        "callback_id": "vy_reconcile_submit",
        "title": plain("Check delivery"),
        "submit": plain("Save verified result"),
        "close": plain("Cancel"),
        "private_metadata": json.dumps({"id": draft["id"], "revision": draft["revision"]}),
        "blocks": [
            section(
                "Check the actual conversation first. Marking a delivered message as not delivered can cause a duplicate when you retry."
            ),
            {
                "type": "input",
                "block_id": "outcome",
                "label": plain("Verified outcome"),
                "element": {
                    "type": "radio_buttons",
                    "action_id": "value",
                    "options": [
                        {
                            "text": plain("The message was delivered"),
                            "value": "confirmed_delivered",
                        },
                        {
                            "text": plain("The message was not delivered"),
                            "value": "confirmed_not_delivered",
                        },
                    ],
                },
            },
            {
                "type": "input",
                "block_id": "message_id",
                "label": plain("Slack message timestamp (required if delivered)"),
                "optional": True,
                "element": {"type": "plain_text_input", "action_id": "value", "max_length": 120},
            },
            {
                "type": "input",
                "block_id": "note",
                "label": plain("What did you verify?"),
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "min_length": 10,
                    "max_length": 1000,
                },
            },
        ],
    }


def draft_blocks(draft, recipient, live):
    value = json.dumps({"id": draft["id"], "revision": draft["revision"]})
    status = draft["status"]
    blocks = [
        section(f"Update for {recipient} · revision {draft['revision']} · {status}"),
        section(
            "Delivered as the VirtualYou bot after your approval."
            if live
            else "Preview mode: approval simulates delivery; no message goes to this person."
        ),
    ]
    # Every character of the reviewed text is visible; no truncated approval preview.
    blocks += [section(draft["text"][i : i + 2800]) for i in range(0, len(draft["text"]), 2800)]
    if draft.get("latest_activity_at"):
        blocks.append(
            {
                "type": "context",
                "elements": [plain("Evidence through " + draft["latest_activity_at"])],
            }
        )
    actions = []
    if status == "pending":
        actions.append(
            button("Approve & send" if live else "Approve preview", "vy_approve", value, "primary")
        )
    if status in {"pending", "approved", "delivery_failed"}:
        actions += [
            button("Edit", "vy_edit", value),
            button("Regenerate", "vy_regenerate", value),
            button("Reject", "vy_reject", value),
        ]
    if status in {"approved", "delivery_failed"}:
        actions.append(
            button(
                "Send approved update" if live else "Simulate approved update",
                "vy_deliver",
                value,
                "primary",
            )
        )
    if status == "delivery_unknown":
        blocks.append(
            section(
                "Delivery is uncertain. Check the destination before retrying; this draft will not resend automatically."
            )
        )
        actions.append(button("Resolve delivery", "vy_reconcile", value))
    if actions:
        blocks.append({"type": "actions", "elements": actions})
    return blocks


def home_view(recipients, drafts, *, connected, live, install_url=None, error=None, status=""):

    blocks = [
        {"type": "header", "text": plain("VirtualYou")},
        section(
            "Choose who you report to. I learn your writing style from your messages to them, prepare updates from your work, and ask you before sending."
        ),
        section(
            "Slack authorization saved. Use Check connection to verify access."
            if connected
            else "Connect your Slack account once to read your own DM history."
        ),
    ]
    if install_url:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": plain("Connect my Slack account"),
                        "url": install_url,
                        "action_id": "vy_connect",
                    }
                ],
            }
        )
    blocks.append(
        {
            "type": "actions",
            "elements": [
                button("Choose a person", "vy_choose"),
                button("Refresh status", "vy_refresh_home"),
                button("Setup & status", "vy_setup"),
                button("Organize work", "vy_projects"),
                button("Check connection", "vy_check_connection"),
            ],
        }
    )
    blocks.append(
        section(
            "Live delivery enabled; every update needs your approval."
            if live
            else "Work reports are in preview mode. Separately enabled DM replies are sent only when you approve their reply card."
        )
    )
    blocks.append(
        section(
            status
            or "Connect Slack → enable work sources → organize projects → choose people → review style and allowed projects."
        )
    )
    for item in recipients[:10]:
        blocks += [
            {"type": "divider"},
            section(
                f"{item.get('name', item['recipient'])}: {item.get('status', 'preparing')} · automatic drafts {'on' if item.get('automatic') else 'off'}"
            ),
        ]
        if item.get("error"):
            blocks.append(section(item["error"]))
        actions = [
            button("Refresh style", "vy_style", item["recipient"]),
            button("Recipient settings", "vy_policy", item["recipient"]),
        ]
        if item.get("status") == "ready":
            blocks.append(
                section(
                    f"Style: {'reviewed' if item.get('reviewed_version') else 'review required'}. Allowed projects: {', '.join(item.get('projects', [])) or 'none selected'}."
                )
            )
            actions += [
                button("Review style", "vy_review_style", item["recipient"]),
                button("Draft now", "vy_draft", item["recipient"]),
                button(
                    "Pause automatic" if item.get("automatic") else "Enable automatic",
                    "vy_toggle",
                    item["recipient"],
                ),
            ]
        blocks.append({"type": "actions", "elements": actions})
    if error:
        blocks.append(
            section(
                "Last workflow issue: "
                + error
                + ". Check the person/status above, then retry the relevant action."
            )
        )
    for draft, name in drafts[:3]:
        blocks.append({"type": "divider"})
        blocks.extend(draft_blocks(draft, name, live))
    return {"type": "home", "blocks": blocks[:100]}
