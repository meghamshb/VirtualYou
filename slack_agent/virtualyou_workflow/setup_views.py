"""Native Slack onboarding and review; no separate dashboard."""

import hashlib
import json

from .views import plain, section


def modal(callback, title, blocks, metadata=None, submit="Save"):
    return {
        "type": "modal",
        "callback_id": callback,
        "title": plain(title),
        "submit": plain(submit),
        "close": plain("Cancel"),
        "private_metadata": json.dumps(metadata or {}),
        "blocks": blocks,
    }


def text_input(key, label, value="", maximum=300, optional=False):
    return {
        "type": "input",
        "block_id": key,
        "optional": optional,
        "label": plain(label),
        "element": {
            "type": "plain_text_input",
            "action_id": "value",
            "max_length": maximum,
            **({"initial_value": str(value)} if value else {}),
        },
    }


def choices(key, label, options, selected=()):
    values = [{"text": plain(title[:75]), "value": value} for title, value in options]
    initial = [o for o in values if o["value"] in selected]
    return {
        "type": "input",
        "block_id": key,
        "optional": True,
        "label": plain(label),
        "element": {
            "type": "checkboxes",
            "action_id": "value",
            "options": values,
            **({"initial_options": initial} if initial else {}),
        },
    }


def multi_select(key, label, options, selected=()):
    block = choices(key, label, options, selected)
    block["element"]["type"] = "multi_static_select"
    block["element"]["placeholder"] = plain("Choose entries")
    return block


def setup_modal(coordinator):
    prefs = coordinator.preferences()
    return modal(
        "vy_setup_submit",
        "Setup & status",
        [
            section(coordinator.status_summary()),
            section(
                "Enable the normalized work sources you want used in drafts. Your ingestion service must collect these sources separately. This switch controls retrieval, not OS file permissions."
            ),
            section(
                "Selected DM samples are redacted locally before storage and model processing. Pattern-based redaction cannot detect every confidential detail. Review your style examples before use. With a cloud model, sanitized samples and selected work evidence leave this machine."
            ),
            choices(
                "sources",
                "Work sources allowed in drafts",
                [("Claude Code", "claude"), ("Cursor", "cursor"), ("Voice", "voice")],
                prefs["sources"],
            ),
            choices(
                "paused",
                "Drafting",
                [("Pause preparation and sending", "yes")],
                ["yes"] if prefs.get("paused") else [],
            ),
        ],
    )


def session_option(row):
    record = json.loads(row["payload"])
    label = f"{row.get('project_id') or 'Unassigned'} · {record['source']} · {record['timestamp_range']['ended_at'][:10]} · {record.get('end_state') or record['session_id']}"
    return label, hashlib.sha256(row["session_id"].encode()).hexdigest()[:32]


def projects_modal(coordinator):
    rows = coordinator.backend.retrieval.activity_choices()
    if not rows:
        return {
            "type": "modal",
            "title": plain("Organize work"),
            "close": plain("Close"),
            "blocks": [
                section(
                    "No activities have arrived yet. Check your ingestion directory and refresh status."
                )
            ],
        }
    return modal(
        "vy_projects_submit",
        "Organize work",
        [
            section(
                "Assign activities to a project. Only projects explicitly selected for a person can appear in their drafts. These labels do not change the ingestion records. Flat-directory activities start unassigned. For automatic assignment, have ingestion write normalized activity files into a named project subfolder."
            ),
            text_input("project", "Project name", maximum=80),
            multi_select("sessions", "Activities (latest 100)", [session_option(r) for r in rows]),
        ],
    )


def policy_modal(coordinator, recipient):
    value = coordinator.state.recipient(recipient)
    projects = coordinator.backend.retrieval.project_choices()
    blocks = [
        section(
            "Choose which work this person may receive. Changing scope or style requires a fresh draft; reject older pending drafts to replace them."
        )
    ]
    if projects:
        blocks.append(
            multi_select(
                "projects",
                "Allowed projects",
                [(p, p) for p in projects[:100]],
                value.get("projects", []),
            )
        )
    else:
        blocks.append(
            section(
                "No projects yet. Use Organize work first. No work is shared until projects are selected."
            )
        )
    blocks.extend(
        [
            text_input(
                "purpose",
                "Reporting purpose",
                value.get("purpose", "Progress update"),
                maximum=1000,
            ),
            text_input(
                "interval",
                "Minimum minutes between automatic drafts",
                str(
                    int(
                        value.get("interval_seconds", coordinator.config.minimum_interval_seconds)
                        / 60
                    )
                ),
                maximum=5,
            ),
            choices(
                "reply",
                "Reply assistance",
                [("Allow Draft update for request shortcut in this person's DM", "yes")],
                ["yes"] if value.get("reply_enabled") else [],
            ),
        ]
    )
    return modal("vy_policy_submit", "Recipient settings", blocks, {"recipient": recipient})


def style_modal(coordinator, recipient):
    profile = coordinator.backend.store.get_persona(coordinator.profile_id(recipient))
    style = profile["style"]
    blocks = [
        section(
            f"Style for {profile['display_name']} · version {profile['version']}. These are estimates from your writing. Correct them before saving. A refresh will require another review."
        )
    ]
    for key, label, maximum in [
        ("tone", "Tone", 300),
        ("greeting", "Greeting", 120),
        ("sign_off", "Sign-off", 120),
        ("sentence_style", "Sentence style", 300),
        ("punctuation", "Punctuation", 300),
        ("emoji", "Emoji use", 300),
    ]:
        blocks.append(
            text_input(
                key,
                label,
                style[key],
                maximum=maximum,
                optional=key not in {"tone", "sentence_style"},
            )
        )
    opts = [{"text": plain(v.title()), "value": v} for v in ["casual", "neutral", "formal"]]
    blocks.append(
        {
            "type": "input",
            "block_id": "formality",
            "label": plain("Formality"),
            "element": {
                "type": "static_select",
                "action_id": "value",
                "options": opts,
                "initial_option": next(o for o in opts if o["value"] == style["formality"]),
            },
        }
    )
    blocks.append(
        text_input(
            "vocabulary",
            "Preferred vocabulary (comma separated)",
            ", ".join(style["vocabulary"]),
            maximum=3000,
            optional=True,
        )
    )
    for i, example in enumerate(profile["examples"]):
        blocks.append(section(f"Stored example {i + 1}"))
        blocks.extend(section(example[n : n + 2800]) for n in range(0, len(example), 2800))
    blocks.append(
        choices(
            "remove",
            "Stored examples",
            [("Remove all retained examples; keep the style description", "yes")],
        )
    )
    return modal(
        "vy_style_review_submit",
        "Review writing style",
        blocks,
        {"recipient": recipient, "version": profile["version"]},
        submit="Save reviewed style",
    )
