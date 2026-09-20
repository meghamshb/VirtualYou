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
        from virtual_you.backend.slack_policy import guard_slack_policy
        self.state.draft_link(draft_id)
        guard_slack_policy(self.backend.store, self.backend.store.get_draft(draft_id))

    def status_summary(self):
        heartbeat = self.backend.store.metadata("heartbeat") or {}
        preferences = self.preferences()
        return (
            f"Activity: {heartbeat.get('state', 'starting')} · {self.backend.retrieval.stats()['count']} indexed records\n"
            f"Sources allowed in drafts: {', '.join(preferences['sources']) or 'none'}\n"
            f"Drafting: {'paused' if preferences.get('paused') else 'running'} · Model: {self.backend.settings.provider} {self.backend.settings.model}"
            + (" All personal DMs enabled; sender-specific styles; approval required."
               if getattr(getattr(self, "dm_replies", None), "all_personal_dms", False)
               else " Personal DM listener enabled; replies require approval." if getattr(self, "dm_replies", None) else "")
        )

    def integration_status_summary(self):
        from virtual_you.backend.activity_feed import integration_summary

        integrations = integration_summary(self.backend.settings, self.backend)
        lines = ["Optional work integrations"]
        for key, label in (("github", "GitHub"), ("jira", "Jira"), ("drive", "Drive")):
            value = integrations[key]
            state = ("configured · enabled" if value.get("enabled") else "configured · disabled") if value.get("status") == "configured" else "not configured"
            lines.append(f"{label}: {state}")
        lines.append("Configuration is not a live connection test. Manage credentials in the local backend configuration; never paste them into Slack.")
        return "\n".join(lines)

    def apply_experience(self, job):
        data = job["payload"]
        if job["kind"] == "preferences":
            sources = data.get("sources", [])
            if any(s not in {"claude", "cursor", "codex", "voice", "git", "github", "jira", "drive"} for s in sources):
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
