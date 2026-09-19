"""Owner-controlled source, audience, and style settings for the Slack experience."""

import hashlib
import json

from virtual_you.backend.errors import ServiceError
from virtual_you.contracts.reporting import PersonaStyle

from .history import slack_call


class Experience:
    def preferences(self):
        return self.backend.store.metadata("slack_preferences") or {"sources": [], "paused": False}

    def policy_fingerprint(self, value):
        return hashlib.sha256(
            json.dumps(
                {
                    "projects": sorted(value.get("projects", [])),
                    "sources": sorted(self.preferences()["sources"]),
                    "purpose": value.get("purpose", ""),
                    "style_version": value.get("reviewed_version"),
                    "reply_enabled": value.get("reply_enabled", False),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def require_ready(self, value):
        if self.preferences().get("paused"):
            raise ServiceError("workflow_paused", "Resume drafting in Setup & status first.")
        profile = self.backend.store.get_persona(self.profile_id(value["recipient"]))
        if value.get("reviewed_version") != profile["version"]:
            raise ServiceError(
                "style_review_required", "Review and save this person's style first."
            )
        if not value.get("projects") or not self.preferences()["sources"]:
            raise ServiceError(
                "scope_required", "Enable work sources and choose this person's projects first."
            )

    def require_current_policy(self, draft_id):
        link = self.state.draft_link(draft_id)
        value = self.state.recipient(link["recipient"])
        self.require_ready(value)
        saved = self.backend.store.metadata("slack_draft_policy:" + draft_id)
        if saved != self.policy_fingerprint(value):
            raise ServiceError(
                "audience_changed",
                "Settings or style changed. Reject this draft and prepare a fresh one.",
            )
        # A session can be reassigned after drafting. Check the current project mapping too.
        draft = self.backend.store.get_draft(draft_id)
        session_ids = {e["session_id"] for e in draft.get("evidence", [])}
        with self.backend.store.connection() as db:
            for session_id in session_ids:
                row = db.execute(
                    "SELECT project_id FROM activity_projects WHERE session_id=?", (session_id,)
                ).fetchone()
                if not row or row[0] not in value.get("projects", []):
                    raise ServiceError(
                        "audience_changed",
                        "Evidence was moved to another project. Reject this draft and create a fresh one.",
                    )

    def status_summary(self):
        heartbeat = self.backend.store.metadata("heartbeat") or {}
        preferences = self.preferences()
        return (
            f"Model: {self.backend.settings.provider} {self.backend.settings.model}. "
            f"Activity refresh: {heartbeat.get('state', 'starting')}. "
            f"Indexed activities: {self.backend.retrieval.stats()['count']}. "
            f"Enabled sources: {', '.join(preferences['sources']) or 'none'}. "
            f"Drafting: {'paused' if preferences.get('paused') else 'running'}."
            + (" All personal DMs enabled; sender-specific styles; approval required."
               if getattr(getattr(self, "dm_replies", None), "all_personal_dms", False)
               else " Personal DM listener enabled; replies require approval." if getattr(self, "dm_replies", None) else "")
        )

    def apply_experience(self, job):
        data = job["payload"]
        if job["kind"] == "preferences":
            sources = data.get("sources", [])
            if any(s not in {"claude", "cursor", "codex", "voice"} for s in sources):
                raise ServiceError("invalid_sources", "Choose supported work sources.")
            self.backend.store.set_metadata(
                "slack_preferences", {"sources": sources, "paused": bool(data.get("paused"))}
            )
        elif job["kind"] == "projects":
            self.backend.retrieval.assign_project(data["sessions"], data["project"])
        elif job["kind"] == "policy":
            value = self.state.recipient(data["recipient"])
            if any(p not in self.backend.retrieval.project_choices() for p in data["projects"]):
                raise ServiceError("invalid_project", "Choose an existing project.")
            interval = int(data["interval"])
            if not 1 <= interval <= 10080:
                raise ServiceError(
                    "invalid_interval", "Choose a cadence between 1 minute and 7 days."
                )
            value.update(
                projects=data["projects"],
                purpose=data["purpose"],
                interval_seconds=interval * 60,
                reply_enabled=bool(data.get("reply_enabled")),
            )
            self.state.save_recipient(value)
        elif job["kind"] == "style_review":
            value = self.state.recipient(data["recipient"])
            profile = self.backend.persona.revise(
                self.profile_id(data["recipient"]),
                data["version"],
                PersonaStyle.model_validate(data["style"]),
                remove_examples=data.get("remove_examples", False),
            )
            value.update(
                reviewed_version=profile.version, persona_version=profile.version, error=None
            )
            self.state.save_recipient(value)
            # Sensitive example removals also clear prior review job payloads.
            data = {**data, "style": profile.style.model_dump()}
            self.state.progress(job, data)
        elif job["kind"] == "check_connection":
            token = self.credentials.user_token()
            if not token:
                raise ServiceError("slack_not_connected", "Use Connect Slack first.")
            client = self.client_factory(token=token, timeout=15, retry_handlers=[])
            result = slack_call(client.auth_test)
            if (
                result.get("user_id") != self.config.owner_id
                or result.get("team_id") != self.config.team_id
            ):
                raise ServiceError("wrong_user_token", "Reconnect the configured Slack account.")
            self.bot()
            self.backend.store.set_metadata("slack_connection_checked", {"ok": True})
