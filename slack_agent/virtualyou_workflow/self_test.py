"""Opt-in owner self-DM testing alongside, never instead of, colleague routing."""

from __future__ import annotations

import asyncio
import os
import re
import time
from decimal import Decimal, InvalidOperation

from virtual_you.backend.errors import ServiceError
from virtual_you.ingest.redact import redact_text

from .dm_replies import DMReplies
from .history import slack_call
from .user_delivery import verified_owner_client

PREFIX = re.compile(r"^(?:vy-test:\s*|/vy-test\s+)(\S[\s\S]*)$")


class _SelfTestState:
    """Test audience lives outside colleague records and persona discovery."""

    def __init__(self, original, person):
        self.original, self.person = original, person

    def recipient(self, recipient):
        if recipient == self.person["recipient"]:
            return dict(self.person)
        return self.original.recipient(recipient)

    def __getattr__(self, name):
        return getattr(self.original, name)


class _SelfTestCoordinator:
    def __init__(self, original, person):
        self.original = original
        self.state = _SelfTestState(original.state, person)

    def __getattr__(self, name):
        return getattr(self.original, name)


class SelfTestDMReplies(DMReplies):
    def __init__(self, coordinator, channel, projects, *, recover=True, reset_activation=False):
        person = {
            "recipient": coordinator.config.owner_id,
            "name": "Yourself · self-test",
            "human_channel": channel,
            "projects": list(projects),
            "reviewed_version": None,
            "reply_enabled": True,
            "automatic": False,
        }
        self.channel = channel
        self.self_verified_token = None
        super().__init__(
            _SelfTestCoordinator(coordinator, person),
            coordinator.config.owner_id,
            recover=recover,
            allow_generic=True,
        )
        self.key = "dm_self_test:" + self.recipient + ":" + channel
        store = self.c.backend.store
        if reset_activation or not store.metadata(self.key):
            now = str(time.time())
            store.set_metadata(self.key, {"oldest": now, "cursor": "", "newest": "0"})
            store.set_metadata(self.key + ":activated", {"at": now})
            # A changed/re-enabled test scope cannot reuse earlier approvals.
            with store.connection(write=True) as db:
                db.execute(
                    "UPDATE slack_dm_replies SET state='self_test_disabled' WHERE recipient=? "
                    "AND state IN ('queued','generated','pending')",
                    (self.recipient,),
                )

    def accepts_event(self, event):
        return event.get("user") == self.recipient and event.get("channel") == self.channel

    def receive_event(self, event, team_id):
        if self._incoming_text(event.get("channel"), event) is not None:
            super().receive_event(event, team_id)

    def get(self, reply_id):
        row = super().get(reply_id)
        if row["channel"] != self.channel:
            raise ServiceError("not_self_dm", "This test belongs to a different self-DM.", 409)
        return row

    def blocks(self, row, status=None):
        # Self-testing has no colleague persona to teach. Preserve normal review,
        # editing and evidence controls, but never offer style-learning actions.
        return super().blocks(
            {
                **row,
                "original_reply": None,
                "style_source": "neutral self-test style · no colleague profile changed",
            },
            status,
        )

    def _incoming_text(self, channel, message):
        if (
            channel != self.channel
            or message.get("user") != self.recipient
            or message.get("bot_id")
            or message.get("app_id")
            or message.get("subtype")
        ):
            return None
        try:
            timestamp = Decimal(message.get("ts", "0"))
            activated = Decimal(self.c.backend.store.metadata(self.key + ":activated")["at"])
            if not timestamp.is_finite() or timestamp <= activated:
                return None
        except (InvalidOperation, TypeError):
            return None
        match = PREFIX.fullmatch(str(message.get("text", "")).strip())
        return redact_text(match.group(1)).strip()[:4000] if match else None

    async def verify_self_dm(self, *, force=False):
        token = self.c.credentials.user_token()
        if token and token == self.self_verified_token and not force:
            return
        # Verify the owner and workspace using the user token, then ensure the
        # selected DM is actually the owner's saved-messages conversation.
        client = await verified_owner_client(self.c, self.recipient, self.channel)
        info = await asyncio.to_thread(slack_call, client.conversations_info, channel=self.channel)
        dm = info.get("channel", {})
        if (
            not dm.get("is_im")
            or dm.get("user") != self.recipient
            or dm.get("id", self.channel) != self.channel
        ):
            raise ServiceError(
                "not_self_dm", "Choose the configured owner's own Slack DM for self-testing.", 409
            )
        self.self_verified_token = token
        self.verified_token = token

    async def poll(self):
        if time.monotonic() < self.next_poll or self.c.preferences().get("paused"):
            return
        await self.verify_self_dm()
        await super().poll()

    async def prepare_one(self):
        if self.c.preferences().get("paused"):
            return
        with self.c.backend.store.connection() as db:
            pending = db.execute(
                "SELECT 1 FROM slack_dm_replies WHERE recipient=? AND channel=? "
                "AND state IN ('queued','generated') LIMIT 1",
                (self.recipient, self.channel),
            ).fetchone()
        if pending:
            await self.verify_self_dm()
            await super().prepare_one()

    async def decide(self, reply_id, approve, **kwargs):
        row = self.get(reply_id)
        if row["channel"] != self.channel:
            raise ServiceError("not_self_dm", "This test belongs to a different self-DM.", 409)
        if approve and row["state"] == "pending":
            await self.verify_self_dm(force=True)
        await super().decide(reply_id, approve, **kwargs)


class SelfTestRouter:
    def __init__(self, coordinator, normal, self_test):
        self.c, self.normal, self.self_test = coordinator, normal, self_test
        self.all_personal_dms = getattr(normal, "all_personal_dms", False)

    def monitor(self, recipient):
        if recipient == self.c.config.owner_id:
            return self.self_test
        if self.normal and hasattr(self.normal, "monitor"):
            return self.normal.monitor(recipient)
        if self.normal and self.normal.recipient == recipient:
            return self.normal
        raise ServiceError("reply_not_found", "This conversation is not being monitored.", 404)

    def receive_event(self, event, team_id):
        if event.get("user") == self.c.config.owner_id:
            self.self_test.receive_event(event, team_id)
        elif self.normal:
            self.normal.receive_event(event, team_id)

    def get(self, reply_id):
        with self.c.backend.store.connection() as db:
            row = db.execute(
                "SELECT recipient FROM slack_dm_replies WHERE id=?", (reply_id,)
            ).fetchone()
        if not row:
            raise ServiceError("reply_not_found", "Reply not found.", 404)
        return self.monitor(row["recipient"]).get(reply_id)

    async def decide(self, reply_id, approve, **kwargs):
        row = self.get(reply_id)
        await self.monitor(row["recipient"]).decide(reply_id, approve, **kwargs)

    async def run(self):
        async with asyncio.TaskGroup() as group:
            if self.normal:
                group.create_task(self.normal.run())
            group.create_task(self.self_test.run())


def configure_self_test(coordinator, normal, environ=None):
    env = os.environ if environ is None else environ
    enabled = env.get("VIRTUAL_YOU_SELF_TEST_ENABLED", "false").lower()
    if enabled not in {"true", "false", "1", "0"}:
        raise ValueError("VIRTUAL_YOU_SELF_TEST_ENABLED must be true or false.")
    store = coordinator.backend.store
    previous = store.metadata("slack_self_test") or {}
    if enabled not in {"true", "1"}:
        if previous.get("enabled"):
            store.set_metadata("slack_self_test", {**previous, "enabled": False})
        return normal
    channel = env.get("VIRTUAL_YOU_SELF_TEST_CHANNEL", "").strip()
    projects = list(
        dict.fromkeys(
            value.strip()
            for value in env.get("VIRTUAL_YOU_SELF_TEST_PROJECTS", "").split(",")
            if value.strip()
        )
    )
    if (
        not re.fullmatch(r"D[A-Z0-9]+", channel)
        or not projects
        or any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", project) for project in projects
        )
    ):
        raise ValueError("Self-testing requires one Slack DM channel and explicit project IDs.")
    config = {"enabled": True, "channel": channel, "projects": projects}
    monitor = SelfTestDMReplies(
        coordinator, channel, projects, recover=normal is None, reset_activation=previous != config
    )
    store.set_metadata("slack_self_test", config)
    return SelfTestRouter(coordinator, normal, monitor)
