"""One existing backend and coordinator per Slack owner; no global token/env mutation."""

import asyncio
import hashlib
import json
from contextlib import AsyncExitStack
from types import SimpleNamespace

from virtual_you.backend.app import create_app
from virtual_you.backend.config import Settings
from virtual_you.contracts.reporting import (
    ApprovalDecision,
    Destination,
    DraftRequest,
    PersonaSeed,
    RetrievalRequest,
    RevisionRequest,
)
from virtual_you.ingest.redact import redact_value


class HostedCredentials:
    def __init__(self, oauth, account):
        self.oauth, self.account = oauth, account

    def data(self):
        return self.oauth.tokens(self.account, "slack")

    def installation(self):
        token = self.data()
        return SimpleNamespace(
            user_token=token["authed_user"]["access_token"],
            bot_token=token["access_token"],
            user_scopes=token["authed_user"]["scope"].split(","),
        )

    def user_token(self):
        return self.installation().user_token

    def bot_token(self):
        return self.installation().bot_token

    def connected(self):
        return True


class Runtimes:
    def __init__(self, settings, oauth, vault):
        self.settings, self.oauth, self.vault = settings, oauth, vault
        self.active = {}
        self.lock = asyncio.Lock()

    async def get(self, account):
        async with self.lock:
            if account in self.active:
                current = self.active[account]
                if not current.worker.done():
                    return current
                await asyncio.gather(current.worker, return_exceptions=True)
                await current.stack.aclose()
                del self.active[account]
            from virtualyou_workflow.config import SlackSettings
            from virtualyou_workflow.coordinator import Coordinator
            from virtualyou_workflow.dm_inbox import DMInbox

            token = await asyncio.to_thread(self.oauth.tokens, account, "slack")
            config = Settings(
                data_dir=self.settings.data_dir / "accounts" / account,
                api_key=hashlib.sha256(
                    (self.settings.encryption_key + account).encode()
                ).hexdigest(),
                provider="openai",
                model=self.settings.model,
                openai_api_key=self.settings.openai_key,
                live_delivery=True,
                heartbeat_enabled=True,
            )
            app = create_app(config)
            stack = AsyncExitStack()
            await stack.enter_async_context(app.router.lifespan_context(app))
            try:
                coordinator = Coordinator(
                    app.state,
                    SlackSettings(token["user_id"], token["team_id"]),
                    HostedCredentials(self.oauth, account),
                )
                coordinator.dm_replies = DMInbox(coordinator)
                # No scope or colleague is implicitly enabled by installing Slack.
                value = SimpleNamespace(app=app, coordinator=coordinator, stack=stack)
                from listeners import register_listeners
                from slack_bolt import App
                from slack_bolt.authorization import AuthorizeResult

                def authorize(**kwargs):
                    current = oauth_tokens = self.oauth.tokens(account, "slack")
                    return AuthorizeResult(
                        enterprise_id=None,
                        team_id=current["team_id"],
                        user_id=current["user_id"],
                        bot_token=oauth_tokens["access_token"],
                        user_token=oauth_tokens["authed_user"]["access_token"],
                    )

                value.bolt = App(
                    authorize=authorize,
                    signing_secret=self.settings.signing_secret,
                    request_verification_enabled=False,
                )
                register_listeners(value.bolt, coordinator)
                value.worker = asyncio.create_task(coordinator.run())
                self.active[account] = value
                return value
            except Exception:
                await stack.aclose()
                raise

    async def stop(self, account):
        async with self.lock:
            item = self.active.pop(account, None)
            if item:
                item.worker.cancel()
                await asyncio.gather(item.worker, return_exceptions=True)
                await item.stack.aclose()

    async def close(self):
        for account in list(self.active):
            await self.stop(account)

    async def upload(self, account, project, records, origin="device"):
        runtime = await self.get(account)
        if not records and origin.startswith("resource:"):
            runtime.app.state.retrieval.remove_origin(origin)
        for record in records:
            runtime.app.state.retrieval.upsert(record, origin)
            runtime.app.state.retrieval.assign_project([record["session_id"]], project)

    async def drafts(self, account):
        runtime = await self.get(account)
        return runtime.app.state.store.list_drafts()

    async def question(self, account, question, projects):
        runtime = await self.get(account)
        state = runtime.app.state
        # Setup test targets only the authenticated owner's app DM; not a colleague.
        token = await asyncio.to_thread(self.oauth.tokens, account, "slack")
        response = await asyncio.to_thread(
            self.oauth.json,
            "POST",
            "https://slack.com/api/conversations.open",
            headers={"Authorization": "Bearer " + token["access_token"]},
            json={"users": token["user_id"]},
        )
        channel = response["channel"]["id"]
        state.settings.slack_channels = tuple(set(state.settings.slack_channels) | {channel})
        state.settings.slack_bot_token = token["access_token"]
        profile = "onboarding-self-test"
        try:
            state.store.get_persona(profile)
        except Exception:
            await state.persona.create(
                PersonaSeed(recipient_id=profile, display_name="Your setup test", messages=[])
            )
        return await state.workflow.create(
            DraftRequest(
                recipient_id=profile,
                question=question,
                retrieval=RetrievalRequest(project_ids=projects, query=question, limit=8),
                destination=Destination(target=channel),
            )
        )

    async def decide(self, account, draft_id, revision, approve):
        runtime = await self.get(account)
        workflow = runtime.app.state.workflow
        workflow.decide(
            draft_id,
            ApprovalDecision(expected_revision=revision, action="approve" if approve else "reject"),
        )
        if approve:
            token = await asyncio.to_thread(self.oauth.tokens, account, "slack")
            runtime.app.state.settings.slack_bot_token = token["access_token"]
            return await workflow.deliver(draft_id, RevisionRequest(expected_revision=revision))
        return {"status": "rejected"}

    async def pause(self, account, value):
        runtime = await self.get(account)
        prefs = runtime.coordinator.preferences()
        runtime.app.state.store.set_metadata("slack_preferences", dict(prefs, paused=value))

    async def event(self, account, body):
        runtime = await self.get(account)
        from slack_bolt.request import BoltRequest

        event = body.get("event", {})
        if (
            event.get("type") == "app_home_opened"
            and event.get("user") != runtime.coordinator.config.owner_id
        ):
            return
        await asyncio.to_thread(runtime.bolt.dispatch, BoltRequest(body=json.dumps(body)))

    async def interaction(self, account, raw):
        from slack_bolt.request import BoltRequest

        runtime = await self.get(account)
        response = await asyncio.to_thread(
            runtime.bolt.dispatch,
            BoltRequest(body=raw, headers={"content-type": "application/x-www-form-urlencoded"}),
        )
        return response

    async def configure_people(self, account, people, projects):
        runtime = await self.get(account)
        c = runtime.coordinator
        for previous in c.state.recipients():
            if previous["recipient"] not in people:
                previous.update(reply_enabled=False, automatic=False, projects=[])
                c.state.save_recipient(previous)
        for person in people:
            c.select(person, False, "desktop-persona:" + person)
            value = c.state.recipient(person)
            value.update(projects=projects, reply_enabled=False, automatic=False)
            c.state.save_recipient(value)
        prefs = c.preferences()
        c.backend.store.set_metadata(
            "slack_preferences",
            dict(prefs, sources=["git", "claude", "cursor", "codex", "github", "jira", "drive"]),
        )

    async def people(self, account):
        runtime = await self.get(account)
        values = []
        for person in runtime.coordinator.state.recipients():
            try:
                profile = runtime.app.state.store.get_persona(
                    runtime.coordinator.profile_id(person["recipient"])
                )
                values.append(
                    {
                        "id": person["recipient"],
                        "name": person.get("name", person["recipient"]),
                        "style": profile["style"],
                        "version": profile["version"],
                        "enabled": person.get("reply_enabled", False),
                    }
                )
            except Exception:
                values.append(
                    {
                        "id": person["recipient"],
                        "name": person.get("name", person["recipient"]),
                        "preparing": True,
                    }
                )
        return redact_value(values)

    async def enable_person(self, account, person, version):
        runtime = await self.get(account)
        c = runtime.coordinator
        profile = c.backend.store.get_persona(c.profile_id(person))
        if profile["version"] != version:
            raise ValueError("style_changed")
        value = c.state.recipient(person)
        value.update(reviewed_version=version, reply_enabled=True, automatic=False)
        c.state.save_recipient(value)

    async def revoke_source(self, account, provider):
        runtime = await self.get(account)
        for origin in runtime.app.state.retrieval.origins():
            if origin.startswith("resource:" + provider + ":"):
                runtime.app.state.retrieval.remove_origin(origin)

    async def drop_resource(self, account, provider, resource):
        runtime = await self.get(account)
        runtime.app.state.retrieval.remove_origin("resource:" + provider + ":" + resource)
