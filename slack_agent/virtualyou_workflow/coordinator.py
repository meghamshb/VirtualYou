import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

from slack_sdk import WebClient
from virtual_you.backend.errors import ServiceError
from virtual_you.contracts.reporting import (
    ApprovalDecision,
    DraftRequest,
    EditRequest,
    PersonaSeed,
    ReconcileDelivery,
    RetrievalRequest,
    RevisionRequest,
)
from virtual_you.ingest.redact import redact_text

from .experience import Experience
from .history import HistoryCollector, RetryLater, slack_call
from .member4 import Member4
from .state import SlackState
from .user_delivery import OwnerDMSender
from .views import draft_blocks, home_view


class Coordinator(Member4, Experience):
    def __init__(self, backend, config, credentials, *, client_factory=WebClient):
        self.backend, self.config, self.credentials = backend, config, credentials
        self.state = SlackState(backend.store)
        self.client_factory = client_factory
        self.verified_bot_token = None
        identity = {"owner": config.owner_id, "team": config.team_id}
        stored = backend.store.metadata("slack_identity")
        if stored and stored != identity:
            raise ValueError(
                "This data directory belongs to a different Slack owner/workspace. Use a separate data directory."
            )
        backend.store.set_metadata("slack_identity", identity)
        self.restore_destinations()
        backend.workflow.gateway.user_sender = OwnerDMSender(self)
        backend.voice.validate_confirmation = self.bind_voice_confirmation
        backend.voice.check_upload = self.require_voice_enabled
        backend.voice_targets = self.voice_targets
        self.dm_replies = None
        watched = os.getenv("VIRTUAL_YOU_DM_WATCH_RECIPIENT", "")
        if watched == '*':
            from .dm_inbox import DMInbox
            self.dm_replies = DMInbox(self)
        elif watched:
            from .dm_replies import DMReplies
            self.state.recipient(watched)
            self.dm_replies = DMReplies(self, watched)

    def restore_destinations(self):
        channels = [r["bot_channel"] for r in self.state.recipients() if r.get("bot_channel")]
        self.backend.settings.slack_channels = tuple(
            sorted(set(self.backend.settings.slack_channels) | set(channels))
        )
        self.backend.settings.slack_bot_token = self.credentials.bot_token()

    def bot(self):
        token = self.credentials.bot_token()
        if not token:
            raise ServiceError("bot_not_connected", "Connect the Slack app before continuing.")
        client = self.client_factory(token=token, timeout=15, retry_handlers=[])
        if token != self.verified_bot_token:
            identity = slack_call(client.auth_test)
            if identity.get("team_id") != self.config.team_id:
                raise ServiceError(
                    "wrong_workspace", "The bot is installed in a different workspace.", 403
                )
            self.verified_bot_token = token
        return client

    def authorized(self, body):
        user = body.get("user", {})
        team = body.get("team", {})
        user_id = user.get("id") if isinstance(user, dict) else user
        team_id = team.get("id") if isinstance(team, dict) else team
        return user_id == self.config.owner_id and team_id == self.config.team_id

    def select(self, recipient, automatic, dedupe):
        if recipient == self.config.owner_id:
            raise ServiceError("invalid_recipient", "Select someone other than yourself.")
        try:
            value = self.state.recipient(recipient)
        except ServiceError:
            value = {
                "recipient": recipient,
                "name": recipient,
                "status": "preparing",
                "seen": {},
                "last_draft_at": 0,
            }
        value.update(automatic=automatic, status="preparing", error=None)
        self.state.save_recipient(value)
        self.state.enqueue("persona", {"recipient": recipient}, dedupe)

    def profile_id(self, recipient):
        # Compound Slack IDs can resemble a high-entropy credential to the
        # content redactor. A deterministic hex key keeps identity out of prose.
        identity = f"{self.config.team_id}.{self.config.owner_id}.{recipient}"
        return hashlib.sha256(identity.encode()).hexdigest()

    def scope(self, value=None):
        return RetrievalRequest(
            since=datetime.now(timezone.utc) - timedelta(hours=self.config.lookback_hours),
            limit=10,
            project_ids=(value or {}).get("projects", []),
            sources=self.preferences()["sources"],
        )

    def evidence_state(self, value=None):
        rows = self.backend.retrieval.search(self.scope(value))
        return {row["record"]["session_id"]: row["record_hash"] for row in rows}, rows

    def home(self, install_url=None):
        if not install_url and os.getenv("SLACK_REDIRECT_URI"):
            install_url = urljoin(os.environ["SLACK_REDIRECT_URI"], "/slack/install")
        drafts = []
        for draft in self.state.cards():
            link = self.state.draft_link(draft["id"])
            person = self.state.recipient(link["recipient"])
            drafts.append((draft, person.get("name", person["recipient"])))
        view = home_view(
            self.state.recipients(),
            drafts,
            connected=self.credentials.connected(),
            live=self.backend.settings.live_delivery,
            install_url=install_url,
            status=self.status_summary(),
            error=self.state.latest_error(),
        )
        extra = self.member4_blocks()
        view["blocks"] = view["blocks"][:100 - len(extra)] + extra
        return view

    def publish_home(self):
        slack_call(self.bot().views_publish, user_id=self.config.owner_id, view=self.home())

    async def create_persona(self, job):
        recipient = job["payload"]["recipient"]
        token = self.credentials.user_token()
        if not token:
            raise ServiceError(
                "user_connection_required",
                "Connect your own Slack account first, then click Refresh style.",
            )
        user = self.client_factory(token=token, timeout=15, retry_handlers=[])

        def checkpoint(progress):
            self.state.progress(job, {**job["payload"], "history": progress})

        samples = await asyncio.to_thread(
            HistoryCollector(self.config).collect,
            user,
            recipient,
            job["payload"].get("history", {}),
            checkpoint,
        )
        info = await asyncio.to_thread(slack_call, user.users_info, user=recipient)
        member = info["user"]
        if member.get("is_bot") or member.get("deleted"):
            raise ServiceError("invalid_recipient", "Select an active human member.")
        name = redact_text(
            member.get("profile", {}).get("display_name") or member.get("real_name") or recipient
        )[:120]
        profile = await self.backend.persona.create(
            PersonaSeed(
                recipient_id=self.profile_id(recipient), display_name=name, messages=samples
            )
        )
        # This opens the BOT's DM, never writes as the human whose history was read.
        conversation = await asyncio.to_thread(
            slack_call, self.bot().conversations_open, users=recipient
        )
        value = self.state.recipient(recipient)
        value.update(
            name=name,
            status="ready",
            error=None,
            persona_version=profile.version,
            sample_count=len(samples),
            human_channel=job["payload"].get("history", {}).get("channel"),
            reviewed_version=None,
            bot_channel=conversation["channel"]["id"],
        )
        self.state.save_recipient(value)
        self.restore_destinations()
        self.state.progress(
            job, {"recipient": recipient}
        )  # Do not retain the extra 20 samples in jobs.

    async def create_draft(self, job):
        recipient = job["payload"]["recipient"]
        value = self.state.recipient(recipient)
        if value.get("status") != "ready":
            raise ServiceError(
                "persona_not_ready", "Wait for this person's style profile to finish."
            )
        if self.state.open_draft(recipient) and not job["payload"].get("draft_id"):
            return
        self.require_ready(value)
        if job["payload"].get("reply") and not value.get("reply_enabled"):
            raise ServiceError("reply_disabled", "Reply assistance is disabled for this person.")
        seen, rows = self.evidence_state(value)
        if not rows:
            raise ServiceError(
                "nothing_to_report", "No recent coding activity has been synced yet."
            )
        self.restore_destinations()
        # Claim a stable backend ID before model generation for crash-safe job resumption.
        from uuid import uuid4

        draft_id = job["payload"].get("draft_id") or str(uuid4())
        self.state.progress(job, {**job["payload"], "draft_id": draft_id})
        try:
            draft = self.backend.store.get_draft(draft_id)
        except ServiceError as error:
            if error.code != "draft_not_found":
                raise
            draft = await self.backend.workflow.create(
                DraftRequest(
                    recipient_id=self.profile_id(recipient),
                    retrieval=self.scope(value),
                    question=job["payload"].get("question") or value.get("purpose") or None,
                    destination={"platform": "slack", "target": value["bot_channel"]},
                ),
                draft_id=draft_id,
            )
        if not self.backend.store.metadata("slack_draft_policy:" + draft["id"]):
            self.backend.store.set_metadata(
                "slack_draft_policy:" + draft["id"], self.policy_fingerprint(value)
            )
        self.state.bind_draft(draft["id"], recipient)
        value.update(seen=seen, last_draft_at=time.time(), error=None)
        self.state.save_recipient(value)
        await asyncio.to_thread(self.notify, draft)

    def notify(self, draft):
        link = self.state.draft_link(draft["id"])
        name = self.state.recipient(link["recipient"]).get("name", link["recipient"])
        client = self.bot()
        blocks = draft_blocks(draft, name, self.backend.settings.live_delivery)
        if link["card_ts"]:
            slack_call(
                client.chat_update,
                channel=link["card_channel"],
                ts=link["card_ts"],
                text="VirtualYou update awaiting your review",
                blocks=blocks,
            )
        elif link["notification_state"] == "pending":
            owner_dm = slack_call(client.conversations_open, users=self.config.owner_id)["channel"][
                "id"
            ]
            self.state.card_status(draft["id"], "sending", channel=owner_dm)
            try:
                result = client.chat_postMessage(
                    channel=owner_dm,
                    text="VirtualYou has an update for you to review.",
                    blocks=blocks,
                    unfurl_links=False,
                    unfurl_media=False,
                    client_msg_id=draft["id"],
                )
            except Exception:
                self.state.card_status(draft["id"], "unknown")
                # The draft remains accessible from App Home. Don't duplicate uncertain notifications.
                raise ServiceError(
                    "notification_uncertain",
                    "The review notification may not have arrived. Open VirtualYou Home to review the saved draft.",
                ) from None
            self.state.card_status(draft["id"], "sent", ts=result["ts"])

    async def action(self, job):
        data = job["payload"]
        self.state.draft_link(data["id"])
        if data["action"] not in {"reject", "reconcile"} and not data.get("applied"):
            self.require_current_policy(data["id"])
        revision = RevisionRequest(expected_revision=data["revision"])
        workflow = self.backend.workflow
        if data.get("applied"):
            draft = self.backend.store.get_draft(data["id"])
        elif data["action"] == "approve":
            draft = workflow.decide(
                data["id"], ApprovalDecision(expected_revision=data["revision"], action="approve")
            )
            self.restore_destinations()
            draft = await workflow.deliver(draft["id"], revision)
        elif data["action"] == "deliver":
            self.restore_destinations()
            draft = await workflow.deliver(data["id"], revision)
        elif data["action"] == "reject":
            draft = workflow.decide(
                data["id"], ApprovalDecision(expected_revision=data["revision"], action="reject")
            )
        elif data["action"] == "regenerate":
            draft = await workflow.regenerate(data["id"], revision)
        elif data["action"] == "edit":
            draft = workflow.edit(
                data["id"], EditRequest(expected_revision=data["revision"], text=data["text"])
            )
        elif data["action"] == "reconcile":
            draft = workflow.reconcile(
                data["id"],
                ReconcileDelivery(
                    expected_revision=data["revision"],
                    outcome=data["outcome"],
                    note=data["note"],
                    message_id=data.get("message_id"),
                ),
            )
        else:
            raise ServiceError("invalid_action", "Unsupported draft action.")
        self.state.progress(job, {**data, "applied": True})
        await asyncio.to_thread(self.notify, draft)

    async def process_once(self, lane=None):
        job = self.state.claim(lane)
        if not job:
            return False
        try:
            if job["kind"] == "dm_decision" and self.dm_replies:
                await self.dm_replies.decide(job["payload"]["id"], job["payload"]["approve"])
            elif job["kind"] == "persona":
                await self.create_persona(job)
            elif job["kind"] == "draft":
                await self.create_draft(job)
            elif job["kind"] == "question":
                await self.prepare_question(job)
            elif job["kind"] == "voice_upload":
                await self.prepare_voice(job)
            elif job["kind"] == "voice_confirm":
                await self.confirm_voice(job)
            elif job["kind"] == "action":
                await self.action(job)
            elif job["kind"] in {
                "preferences",
                "projects",
                "policy",
                "style_review",
                "check_connection",
            }:
                await asyncio.to_thread(self.apply_experience, job)
            elif job["kind"] == "toggle":
                value = self.state.recipient(job["payload"]["recipient"])
                value["automatic"] = job["payload"]["automatic"]
                self.state.save_recipient(value)
            self.state.finish(job)
        except RetryLater as error:
            self.state.finish(job, error="slack_rate_limited", retry_after=error.seconds)
        except ServiceError as error:
            self.state.finish(job, error=error.code)
            recipient = job["payload"].get("recipient")
            if recipient:
                value = self.state.recipient(recipient)
                value["error"] = error.message
                if job["kind"] == "persona":
                    value["status"] = "needs_attention"
                self.state.save_recipient(value)
        except Exception:
            # Do not log raw Slack payloads, tokens, or message history.
            self.state.finish(job, error="workflow_failed")
        try:
            await asyncio.to_thread(self.publish_home)
        except Exception:
            pass  # Home will refresh on the next user interaction.
        return True

    def plan_automatic(self):
        refresh = self.backend.store.metadata("heartbeat") or {}
        if refresh.get("state") != "healthy":
            return
        if self.preferences().get("paused"):
            return
        for value in self.state.recipients():
            if (
                not value.get("automatic")
                or value.get("status") != "ready"
                or self.state.open_draft(value["recipient"])
            ):
                continue
            try:
                self.require_ready(value)
            except ServiceError:
                continue
            seen, rows = self.evidence_state(value)
            if not rows:
                continue
            newest = max(datetime.fromisoformat(r["indexed_at"]).timestamp() for r in rows)
            if time.time() - newest < self.config.quiet_seconds:
                continue
            if time.time() - value.get("last_draft_at", 0) < value.get(
                "interval_seconds", self.config.minimum_interval_seconds
            ):
                continue
            if not any(value.get("seen", {}).get(key) != digest for key, digest in seen.items()):
                continue
            fingerprint = hashlib.sha256(
                (json.dumps(seen, sort_keys=True) + self.policy_fingerprint(value)).encode()
            ).hexdigest()
            self.state.enqueue(
                "draft",
                {"recipient": value["recipient"]},
                f"auto:{value['recipient']}:{fingerprint}",
            )

    async def run(self):
        async with asyncio.TaskGroup() as group:
            if self.dm_replies:
                group.create_task(self.dm_replies.run())
            group.create_task(self.run_jobs())
            group.create_task(self.run_voice_jobs())

    async def run_voice_jobs(self):
        while True:
            await self.process_once("voice")
            await asyncio.sleep(0.25)

    async def run_jobs(self):
        next_plan = 0
        while True:
            if time.monotonic() >= next_plan:
                try:
                    self.plan_automatic()
                except Exception:
                    logging.getLogger(__name__).error(
                        "Automatic draft planning failed; retrying on next interval."
                    )
                next_plan = time.monotonic() + self.config.poll_seconds
            await self.process_once("main")
            await asyncio.sleep(0.25)
