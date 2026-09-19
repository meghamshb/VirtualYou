"""Slack transport for the shared Member 4 services. Never approves or sends answers."""

import asyncio
import json
import os
import re
from urllib.parse import urljoin, urlparse
from uuid import NAMESPACE_URL, uuid5

import httpx
from virtual_you.backend.errors import ServiceError
from virtual_you.backend.voice import MAX_AUDIO_BYTES
from virtual_you.contracts.assistant import QuestionRequest, VoiceConfirm
from virtual_you.ingest.redact import redact_text

from .history import slack_call
from .setup_views import modal
from .views import button, plain, section


def option(label, value):
    return {"text": plain(label[:75]), "value": value}


class Member4:
    def queue_question(self, recipient, question, identity):
        value = self.state.recipient(recipient)
        if not value.get("reply_enabled") or self.preferences().get("paused"):
            return False
        # Explicit shortcuts in a human DM reuse its existing durable reply row.
        channel, ts = identity.rsplit(":", 1)
        if channel == value.get("human_channel") and self.dm_replies:
            monitor = (
                self.dm_replies.monitor(recipient)
                if hasattr(self.dm_replies, "monitor")
                else self.dm_replies
            )
            if monitor.recipient == recipient:
                with self.backend.store.connection(write=True) as db:
                    monitor._insert(db, channel, {"user": recipient, "text": question, "ts": ts})
                return True
        request_id = str(uuid5(NAMESPACE_URL, "slack-question:" + identity))
        payload = {
            "recipient": recipient,
            "policy": self.policy_fingerprint(value),
            "request": QuestionRequest(
                request_id=request_id,
                question=redact_text(question)[:1000],
                recipient_id=self.profile_id(recipient),
                retrieval=self.scope(value),
                destination={"target": value["human_channel"], "send_as": "user"},
            ).model_dump(mode="json"),
        }
        self.state.enqueue("question", payload, request_id)
        return True

    def receive_member4(self, event, body, *, mention=False):
        if body.get("team_id") != self.config.team_id or event.get("bot_id"):
            return False
        if self.preferences().get("paused"):
            return False
        # Only a signed owner event in the bot's DM can initiate an audio download.
        if (
            event.get("user") == self.config.owner_id
            and event.get("files")
            and event.get("channel_type") == "im"
            and any(a.get("is_bot") for a in body.get("authorizations", []))
            and not any(
                event.get("channel") == p.get("human_channel") for p in self.state.recipients()
            )
        ):
            try:
                self.require_voice_enabled()
            except ServiceError:
                return True
            for file in event["files"][:3]:
                if re.fullmatch(r"F[A-Z0-9]+", file.get("id", "")):
                    self.state.enqueue(
                        "voice_upload", {"file_id": file["id"]}, "voice:" + file["id"]
                    )
            return True
        if event.get("subtype") or not event.get("text", "").strip():
            return False
        try:
            value = self.state.recipient(event.get("user"))
        except ServiceError:
            return False
        # Personal DMs belong exclusively to the existing DMInbox/DMReplies route.
        if not mention and event.get("channel") != value.get("bot_channel"):
            return False
        question = re.sub(r"<@[A-Z0-9]+>", "", event["text"]).strip()
        if not question or len(question) > 1000 or not event.get("ts"):
            return False
        return self.queue_question(
            value["recipient"], question, event["channel"] + ":" + event["ts"]
        )

    async def prepare_question(self, job):
        data = job["payload"]
        value = self.state.recipient(data["recipient"])
        blocked = None
        try:
            self.require_ready(value)
            if not value.get("reply_enabled") or self.policy_fingerprint(value) != data["policy"]:
                blocked = "audience_changed"
        except ServiceError as error:
            blocked = error.code
        draft_id = str(
            uuid5(NAMESPACE_URL, "virtual-you-question:" + data["request"]["request_id"])
        )
        self.state.bind_draft(draft_id, data["recipient"])
        self.backend.store.set_metadata("slack_draft_policy:" + draft_id, data["policy"])
        result = await self.backend.assistant.ask(
            QuestionRequest.model_validate(data["request"]), blocked_reason=blocked
        )
        if result["status"] == "draft_ready":
            draft = self.backend.store.get_draft(result["draft_id"])
            self.state.bind_draft(draft["id"], data["recipient"])
            self.backend.store.set_metadata("slack_draft_policy:" + draft["id"], data["policy"])
            await asyncio.to_thread(self.notify, draft)
        # Escalations persist and appear in Home; nothing is sent to the requester.

    async def download_voice(self, file_id):
        file = (await asyncio.to_thread(slack_call, self.bot().files_info, file=file_id))["file"]
        if file.get("user") != self.config.owner_id or not file.get("mimetype", "").startswith(
            "audio/"
        ):
            raise ServiceError(
                "unsupported_voice_file", "Upload an audio recording you own to your VirtualYou DM."
            )
        if int(file.get("size", 0)) > MAX_AUDIO_BYTES:
            raise ServiceError("invalid_audio_size", "Choose an audio file up to 8 MiB.")
        url = file.get("url_private_download") or file.get("url_private", "")
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "files.slack.com"
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise ServiceError(
                "invalid_voice_url", "Slack did not provide a supported private file URL."
            )
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
                async with client.stream(
                    "GET", url, headers={"Authorization": "Bearer " + self.credentials.bot_token()}
                ) as response:
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_AUDIO_BYTES:
                            raise ServiceError(
                                "invalid_audio_size", "Choose an audio file up to 8 MiB."
                            )
            return bytes(content)
        except httpx.HTTPError:
            raise ServiceError(
                "voice_download_failed",
                "Could not read the recording. Check files:read and share it with the bot.",
            ) from None

    async def prepare_voice(self, job):
        self.require_voice_enabled()
        file_id = job["payload"]["file_id"]
        await self.backend.voice.upload(
            await self.download_voice(file_id), "slack:" + self.config.team_id + ":" + file_id
        )

    async def confirm_voice(self, job):
        data = job["payload"]
        value = self.state.recipient(data["recipient"])
        self.require_ready(value)
        if "voice" not in self.preferences()["sources"] or data["project"] not in value.get(
            "projects", []
        ):
            raise ServiceError(
                "voice_scope_required",
                "Enable Voice and allow this project for the recipient first.",
            )
        policy = self.policy_fingerprint(value)
        draft_id = str(uuid5(NAMESPACE_URL, "voice-draft:" + data["id"]))
        self.state.bind_draft(draft_id, data["recipient"])
        if not self.backend.store.metadata("slack_draft_policy:" + draft_id):
            self.backend.store.set_metadata("slack_draft_policy:" + draft_id, policy)
        note = await self.backend.voice.confirm(
            data["id"],
            VoiceConfirm(
                expected_revision=data["revision"],
                transcript=data["transcript"],
                recipient_id=self.profile_id(data["recipient"]),
                project=data["project"],
                destination={"target": value["human_channel"], "send_as": "user"},
            ),
        )
        self.state.bind_draft(note["draft_id"], data["recipient"])
        if not self.backend.store.metadata("slack_draft_policy:" + note["draft_id"]):
            self.backend.store.set_metadata("slack_draft_policy:" + note["draft_id"], policy)
        await asyncio.to_thread(self.notify, self.backend.store.get_draft(note["draft_id"]))

    def require_voice_enabled(self):
        if self.preferences().get("paused") or "voice" not in self.preferences()["sources"]:
            raise ServiceError(
                "voice_scope_required", "Enable Voice and resume drafting before transcribing.", 409
            )

    def voice_targets(self):
        return [
            {
                "recipient_id": self.profile_id(v["recipient"]),
                "name": v.get("name", v["recipient"]),
                "projects": v.get("projects", []),
                "destination": {
                    "platform": "slack",
                    "target": v["human_channel"],
                    "send_as": "user",
                },
            }
            for v in self.state.recipients()
            if v.get("human_channel") and v.get("projects")
        ]

    def bind_voice_confirmation(self, note_id, request):
        from virtual_you.backend.slack_policy import bind_voice_policy

        bind_voice_policy(self.backend.store, note_id, request)

    def member4_blocks(self):
        processing = (
            "Audio is uploaded to ElevenLabs for transcription."
            if self.backend.settings.voice_provider == "elevenlabs"
            else "Transcription runs locally on the backend."
        )
        blocks = [
            section(
                "Voice notes: send an audio file (up to 3 minutes / 8 MiB) to your VirtualYou DM. "
                + processing
                + " Review its transcript here before creating a draft."
            )
        ]
        base = os.getenv("SLACK_REDIRECT_URI", "")
        if base:
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": plain("Record memo"),
                            "action_id": "vy_record_memo",
                            "url": urljoin(base, "/review#voice"),
                            "value": "record",
                        }
                    ],
                }
            )
        for note in [
            n
            for n in self.backend.voice.list_notes()
            if n["status"] in {"needs_review", "confirmed"}
        ][:5]:
            if note["status"] in {"needs_review", "confirmed"}:
                blocks += [
                    section(f"Voice memo · {note['created_at']} · {note['status']}"),
                    {
                        "type": "actions",
                        "elements": [button("Review voice memo", "vy_voice_review", note["id"])],
                    },
                ]
        for item in [
            r for r in self.backend.assistant.list_requests() if r["status"] == "escalated"
        ][:5]:
            blocks += [
                section(
                    f"Needs your attention · {item['reason']}\n{item['question']}\n{item['answer']}"
                ),
                {
                    "type": "actions",
                    "elements": [button("Mark handled", "vy_escalation_resolve", item["id"])],
                },
            ]
        return blocks


def register_member4(app, coordinator, event_key):
    @app.action("vy_record_memo")
    def record_link(ack):
        ack()

    @app.action("vy_escalation_resolve")
    def resolve(ack, body):
        ack()
        if coordinator.authorized(body):
            coordinator.backend.assistant.resolve(
                body["actions"][0]["value"], "Handled by owner in Slack; no automatic reply."
            )
            coordinator.publish_home()

    @app.action("vy_voice_review")
    def review(ack, body, client):
        ack()
        if not coordinator.authorized(body):
            return
        note = coordinator.backend.voice.get(body["actions"][0]["value"])
        if note["status"] == "confirmed":
            view = modal(
                "vy_voice_retry",
                "Retry voice draft",
                [
                    section(
                        "The transcript is already confirmed. Retry generation with the same transcript, recipient and project."
                    )
                ],
                {"id": note["id"]},
            )
            client.views_open(trigger_id=body["trigger_id"], view=view)
            return
        recipients = coordinator.state.recipients()
        options = [
            option(v.get("name", v["recipient"]), v["recipient"])
            for v in recipients
            if v.get("status") == "ready" and v.get("projects") and v.get("human_channel")
        ][:100]
        projects = sorted({p for v in recipients for p in v.get("projects", [])})[:100]
        if not options or not projects:
            view = modal(
                "vy_voice_unavailable",
                "Voice memo",
                [section("Choose and review a recipient style before creating a voice draft.")],
            )
            view.pop("submit")
        else:
            view = modal(
                "vy_voice_confirm",
                "Review voice memo",
                [
                    section(note["warning"]),
                    {
                        "type": "input",
                        "block_id": "transcript",
                        "label": plain("Correct the transcript"),
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "value",
                            "multiline": True,
                            "max_length": 3000,
                            "initial_value": note["transcript"][:3000],
                        },
                    },
                    {
                        "type": "input",
                        "block_id": "recipient",
                        "label": plain("Recipient"),
                        "element": {
                            "type": "static_select",
                            "action_id": "value",
                            "options": options,
                        },
                    },
                    {
                        "type": "input",
                        "block_id": "project",
                        "label": plain("Project (must be allowed for the recipient)"),
                        "element": {
                            "type": "static_select",
                            "action_id": "value",
                            "options": [option(p, p) for p in projects],
                        },
                    },
                    section(
                        "Confirming creates a draft for review. It does not approve or send it."
                    ),
                ],
                {"id": note["id"], "revision": note["revision"]},
            )
            if len(note["transcript"]) > 3000:
                view = modal(
                    "vy_voice_unavailable",
                    "Long transcript",
                    [
                        section(
                            "This transcript exceeds Slack's editor limit. Review it through the backend voice page or API."
                        )
                    ],
                )
                view.pop("submit")
        client.views_open(trigger_id=body["trigger_id"], view=view)

    @app.view("vy_voice_confirm")
    def confirm(ack, body, view):
        if not coordinator.authorized(body):
            ack(
                response_action="errors",
                errors={"transcript": "Only the owner may confirm a memo."},
            )
            return
        data = json.loads(view["private_metadata"])
        values = view["state"]["values"]
        data.update(
            transcript=redact_text(values["transcript"]["value"]["value"]),
            recipient=values["recipient"]["value"]["selected_option"]["value"],
            project=values["project"]["value"]["selected_option"]["value"],
        )
        if data["project"] not in coordinator.state.recipient(data["recipient"]).get(
            "projects", []
        ):
            ack(
                response_action="errors",
                errors={"project": "Choose a project allowed for this person."},
            )
            return
        coordinator.state.enqueue("voice_confirm", data, event_key(body, "voice_confirm"))
        ack()

    @app.view("vy_voice_retry")
    def retry(ack, body, view):
        ack()
        if not coordinator.authorized(body):
            return
        note = coordinator.backend.voice.get(json.loads(view["private_metadata"])["id"])
        request = note["confirmed_request"]
        recipient = next(
            (
                v["recipient"]
                for v in coordinator.state.recipients()
                if coordinator.profile_id(v["recipient"]) == request["recipient_id"]
            ),
            None,
        )
        if recipient is None:
            return
        coordinator.state.enqueue(
            "voice_confirm",
            {
                "id": note["id"],
                "revision": request["expected_revision"],
                "recipient": recipient,
                "project": request["project"],
                "transcript": request["transcript"],
            },
            event_key(body, "voice_retry"),
        )
