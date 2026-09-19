"""Persisted Slack audience checks, also enforced when only the HTTP backend runs."""

import hashlib
import json

from virtual_you.backend.errors import ServiceError


def policy_fingerprint(value, preferences):
    return hashlib.sha256(
        json.dumps(
            {
                "projects": sorted(value.get("projects", [])),
                "sources": sorted(preferences["sources"]),
                "purpose": value.get("purpose", ""),
                "style_version": value.get("reviewed_version"),
                "reply_enabled": value.get("reply_enabled", False),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def guard_slack_policy(store, draft):
    with store.connection() as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='slack_drafts'"
        ).fetchone():
            return
        link = db.execute(
            "SELECT recipient FROM slack_drafts WHERE draft_id=?", (draft["id"],)
        ).fetchone()
        if not link:
            return
        row = db.execute(
            "SELECT payload FROM slack_recipients WHERE recipient=?", (link[0],)
        ).fetchone()
        value = json.loads(row[0]) if row else {}
        preferences = store.metadata("slack_preferences") or {"sources": [], "paused": False}
        if preferences.get("paused"):
            raise ServiceError("workflow_paused", "Resume the Slack workflow first.", 409)
        profile = store.get_persona(draft["request"]["recipient_id"])
        if value.get("reviewed_version") != profile["version"]:
            raise ServiceError(
                "style_review_required", "Review the recipient's current style.", 409
            )
        saved = store.metadata("slack_draft_policy:" + draft["id"])
        if (
            not value.get("projects")
            or not preferences["sources"]
            or saved != policy_fingerprint(value, preferences)
            or draft["destination"]["platform"] != "slack"
            or draft["destination"]["target"] != value.get("bot_channel")
        ):
            raise ServiceError(
                "audience_changed", "Audience settings changed. Prepare a fresh draft.", 409
            )
        for item in draft.get("evidence", []):
            mapping = db.execute(
                "SELECT project_id FROM activity_projects WHERE session_id=?", (item["session_id"],)
            ).fetchone()
            if not mapping or mapping[0] not in value["projects"]:
                raise ServiceError(
                    "audience_changed",
                    "Evidence moved to another project. Prepare a fresh draft.",
                    409,
                )
