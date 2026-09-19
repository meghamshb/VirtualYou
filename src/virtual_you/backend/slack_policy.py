"""Persisted Slack audience checks, also enforced when only the HTTP backend runs."""

import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from virtual_you.backend.errors import ServiceError


def scope_slack_question(store, request):
    """Browser questions obey the same recipient scope as Slack-originated ones."""
    identity = store.metadata("slack_identity")
    if not identity:
        return request
    preferences = store.metadata("slack_preferences") or {"sources": [], "paused": False}
    with store.connection() as db:
        people = [json.loads(row[0]) for row in db.execute("SELECT payload FROM slack_recipients")]
    value = next(
        (
            person
            for person in people
            if hashlib.sha256(
                f"{identity['team']}.{identity['owner']}.{person['recipient']}".encode()
            ).hexdigest()
            == request.recipient_id
        ),
        None,
    )
    if (
        not value
        or not value.get("reply_enabled")
        or preferences.get("paused")
        or not value.get("projects")
        or not preferences["sources"]
        or value.get("reviewed_version") != store.get_persona(request.recipient_id)["version"]
        or request.destination.platform != "slack"
        or request.destination.send_as != "user"
        or request.destination.target != value.get("human_channel")
    ):
        raise ServiceError(
            "audience_changed", "Choose an enabled recipient and their personal DM.", 409
        )
    request = request.model_copy(deep=True)
    for field, allowed in (("project_ids", value["projects"]), ("sources", preferences["sources"])):
        selected = getattr(request.retrieval, field)
        if selected is not None and not set(selected).issubset(allowed):
            raise ServiceError(
                "audience_changed", "This evidence is outside the recipient's allowed scope.", 409
            )
        setattr(request.retrieval, field, allowed if selected is None else selected)
    draft_id = str(uuid5(NAMESPACE_URL, "virtual-you-question:" + request.request_id))
    policy = policy_fingerprint(value, preferences)
    saved = store.metadata("slack_draft_policy:" + draft_id)
    if saved and saved != policy:
        raise ServiceError("audience_changed", "Settings changed. Prepare a fresh question.", 409)
    with store.connection(write=True) as db:
        db.execute(
            "INSERT OR IGNORE INTO slack_drafts VALUES(?,?,NULL,NULL,'pending')",
            (draft_id, value["recipient"]),
        )
        db.execute(
            "INSERT OR IGNORE INTO metadata VALUES(?,?)",
            ("slack_draft_policy:" + draft_id, json.dumps(policy)),
        )
    return request


def bind_voice_policy(store, note_id, request):
    """Bind browser and Slack confirmations to the same persisted audience policy."""
    identity = store.metadata("slack_identity")
    if not identity:
        return  # A standalone owner-authenticated backend has no Slack audience registry.
    preferences = store.metadata("slack_preferences") or {"sources": [], "paused": False}
    with store.connection() as db:
        people = [json.loads(row[0]) for row in db.execute("SELECT payload FROM slack_recipients")]
    value = next(
        (
            person
            for person in people
            if hashlib.sha256(
                f"{identity['team']}.{identity['owner']}.{person['recipient']}".encode()
            ).hexdigest()
            == request.recipient_id
        ),
        None,
    )
    profile = store.get_persona(request.recipient_id)
    if (
        not value
        or preferences.get("paused")
        or "voice" not in preferences["sources"]
        or request.project not in value.get("projects", [])
        or value.get("reviewed_version") != profile["version"]
        or request.destination.platform != "slack"
        or request.destination.send_as != "user"
        or request.destination.target != value.get("human_channel")
    ):
        raise ServiceError(
            "voice_scope_required",
            "Review the person's style, enable Voice, and choose an allowed project and personal DM.",
            409,
        )
    draft_id = str(uuid5(NAMESPACE_URL, "voice-draft:" + note_id))
    policy = policy_fingerprint(value, preferences)
    saved = store.metadata("slack_draft_policy:" + draft_id)
    if saved and saved != policy:
        raise ServiceError(
            "audience_changed", "Audience settings changed. Create a fresh voice memo.", 409
        )
    with store.connection(write=True) as db:
        db.execute(
            "INSERT OR IGNORE INTO slack_drafts VALUES(?,?,NULL,NULL,'pending')",
            (draft_id, value["recipient"]),
        )
        db.execute(
            "INSERT OR IGNORE INTO metadata VALUES(?,?)",
            ("slack_draft_policy:" + draft_id, json.dumps(policy)),
        )


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
            or draft["destination"]["target"]
            != value.get(
                "human_channel" if draft["destination"].get("send_as") == "user" else "bot_channel"
            )
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
