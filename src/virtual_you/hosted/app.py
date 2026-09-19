"""Hosted customer onboarding: OAuth, revocable devices and isolated existing backends."""

import asyncio
import hashlib
import hmac
import html
import json
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from virtual_you.backend.app import BodyLimit
from virtual_you.backend.errors import ServiceError
from virtual_you.contracts.activity import ActivityRecord
from virtual_you.ingest.redact import assert_safe_serialized, redact_value

from .config import RelaySettings
from .oauth import PROVIDERS, OAuth, OAuthFailure
from .resources import choices, evidence
from .runtime import Runtimes
from .store import Vault, digest


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Poll(Strict):
    device_code: str = Field(min_length=30, max_length=100)


class Selection(Strict):
    projects: list[str] = Field(min_length=1, max_length=20)
    resources: list[dict[str, str]] = Field(default_factory=list, max_length=30)
    people: list[str] = Field(default_factory=list, max_length=30)


class Upload(Strict):
    project: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    records: list[ActivityRecord] = Field(max_length=30)


class Decision(Strict):
    revision: int = Field(ge=1)
    approve: bool


class Question(Strict):
    text: str = Field(min_length=1, max_length=1000)


class Pause(Strict):
    paused: bool


class Enable(Strict):
    version: int = Field(ge=1)


def page(message, content=""):
    return HTMLResponse(
        '<!doctype html><meta name="viewport" content="width=device-width"><title>VirtualYou</title>'
        '<main style="font:18px system-ui;max-width:540px;margin:10vh auto;padding:24px">'
        "<h1>VirtualYou</h1><p>" + html.escape(message) + "</p>" + content + "</main>",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'",
        },
    )


def create_relay(settings=None, *, transport=None, runtimes_factory=Runtimes):
    settings = settings or RelaySettings.from_env()
    vault = Vault(settings.data_dir / "relay.sqlite3", settings.encryption_key)
    oauth = OAuth(settings, vault, transport)
    runtimes = runtimes_factory(settings, oauth, vault)
    rate = defaultdict(deque)
    account_locks = defaultdict(asyncio.Lock)

    async def refresh(account):
        async with account_locks[account]:
            setup = vault.get("setup", account) or {}
            results = []
            for selection in setup.get("resources", []):
                try:
                    record = await asyncio.to_thread(evidence, oauth, account, selection)
                    await runtimes.upload(
                        account,
                        selection["project"],
                        [record] if record else [],
                        origin="resource:" + selection["provider"] + ":" + selection["resource"],
                    )
                    results.append({"provider": selection["provider"], "ok": True})
                except Exception:
                    results.append({"provider": selection["provider"], "ok": False})
            setup.update(last_refresh=time.time(), refresh_results=results)
            vault.put("setup", account, setup)
            return results

    @asynccontextmanager
    async def lifespan(app):
        async def heartbeat():
            while True:
                for account in vault.keys("setup"):
                    setup = vault.get("setup", account) or {}
                    if setup.get("step") == "complete" and not setup.get("paused"):
                        try:
                            await runtimes.get(account)
                            await refresh(account)
                        except Exception:
                            pass  # Status endpoints expose failures without private exception data.
                await asyncio.sleep(120)

        async def event_worker():
            while True:
                for key in vault.keys("event"):
                    job = vault.get("event", key)
                    if job:
                        try:
                            await runtimes.event(job["account"], job["body"])
                            vault.delete("event", key)
                        except Exception:
                            pass
                await asyncio.sleep(1)

        events_worker = asyncio.create_task(event_worker())
        worker = asyncio.create_task(heartbeat())
        try:
            yield
        finally:
            worker.cancel()
            events_worker.cancel()
            await asyncio.gather(worker, events_worker, return_exceptions=True)
            await runtimes.close()
            oauth.http.close()

    app = FastAPI(title="VirtualYou customer relay", lifespan=lifespan)
    app.add_middleware(BodyLimit)
    app.state.vault, app.state.oauth, app.state.runtimes = vault, oauth, runtimes

    @app.middleware("http")
    async def harden(request, call_next):
        key = request.client.host if request.client else "unknown"
        now = time.time()
        bucket = rate[key]
        while bucket and bucket[0] < now - 60:
            bucket.popleft()
        if len(bucket) >= 120:
            return JSONResponse(
                {"error": "rate_limited"}, status_code=429, headers={"Retry-After": "60"}
            )
        bucket.append(now)
        if len(rate) > 10000:
            for old in list(rate):
                if not rate[old] or rate[old][-1] < now - 60:
                    del rate[old]
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(OAuthFailure)
    async def oauth_error(request, exc):
        return JSONResponse({"error": exc.code}, status_code=409)

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return JSONResponse({"error": exc.code}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"error": "invalid_request"}, status_code=422)

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"error": "invalid_selection_or_changed_state"}, status_code=400)

    bearer = HTTPBearer(auto_error=False)

    def device(auth: HTTPAuthorizationCredentials = Depends(bearer)):
        value = vault.get("device", digest(auth.credentials)) if auth else None
        if not value:
            raise HTTPException(401, "Reconnect this device.")
        return value

    @app.get("/healthz")
    def health():
        return {
            "status": "ok",
            "version": "0.3.0",
            "providers": {p: oauth.configured(p) for p in PROVIDERS},
            "model_ready": bool(settings.openai_key),
            "public_origin": settings.base_url,
        }

    @app.post("/v1/devices/pair")
    def pair():
        if not oauth.configured("slack"):
            raise OAuthFailure("service_not_configured")
        device_code, public = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
        vault.put(
            "pair",
            public,
            {"code": digest(code), "device": digest(device_code), "attempts": 0},
            ttl=600,
        )
        vault.put("poll", digest(device_code), {"state": "pending"}, ttl=600)
        return {
            "device_code": device_code,
            "user_code": code,
            "verification_uri": settings.base_url + "/pair/" + public,
            "expires_at": time.time() + 600,
            "interval": 5,
        }

    @app.get("/pair/{public}")
    def verify(public: str):
        if not vault.get("pair", public):
            return page("This connection request expired. Start again in the desktop app.")
        return page(
            "Enter the code shown in YOUR VirtualYou desktop app. Do not enter a code sent by someone else.",
            '<form method="post"><label>Device code <input name="code" maxlength="8" autocomplete="off" required></label><button>Connect my Slack account</button></form>',
        )

    @app.post("/pair/{public}")
    async def begin(public: str, request: Request):
        value = vault.get("pair", public, consume=True)
        received = parse_qs((await request.body()).decode()).get("code", [""])[0].upper().strip()
        if not value or not hmac.compare_digest(value["code"], digest(received)):
            return page("The code did not match. Start again in the desktop app.")
        cookie = secrets.token_urlsafe(32)
        url = oauth.start("slack", {"pair_device": value["device"]}, cookie)
        response = RedirectResponse(url, 303)
        response.set_cookie(
            "vy_oauth",
            cookie,
            max_age=600,
            secure=not settings.development,
            httponly=True,
            samesite="lax",
            path="/oauth",
        )
        return response

    @app.post("/v1/devices/poll")
    def poll(payload: Poll):
        key = digest(payload.device_code)
        with vault.lock:
            value = vault.get("poll", key)
            if not value:
                return {"state": "expired"}
            if value["state"] != "pending":
                vault.delete("poll", key)
            return value

    @app.post("/v1/integrations/{provider}/authorize")
    def authorize(provider: str, who=Depends(device)):
        if not oauth.configured(provider):
            raise OAuthFailure("service_not_configured")
        nonce = secrets.token_urlsafe(32)
        vault.put(
            "launch", digest(nonce), {"account": who["account"], "provider": provider}, ttl=300
        )
        return {"url": settings.base_url + "/oauth/launch/" + nonce}

    @app.get("/oauth/launch/{nonce}")
    def launch(nonce: str):
        context = vault.get("launch", digest(nonce), consume=True)
        if not context:
            return page("This sign-in link expired. Reconnect in VirtualYou.")
        cookie = secrets.token_urlsafe(32)
        url = oauth.start(context["provider"], context, cookie)
        response = RedirectResponse(url, 302)
        response.set_cookie(
            "vy_oauth",
            cookie,
            max_age=600,
            secure=not settings.development,
            httponly=True,
            samesite="lax",
            path="/oauth",
        )
        return response

    @app.get("/oauth/{provider}/callback")
    async def callback(
        provider: str, request: Request, state: str = "", code: str = "", error: str = ""
    ):
        context = vault.get("oauth", digest(state), consume=True)
        if (
            not context
            or context["provider"] != provider
            or not hmac.compare_digest(
                context["cookie"], digest(request.cookies.get("vy_oauth", ""))
            )
        ):
            return page("Authorization could not be verified. Reconnect in the app.")
        try:
            if error or not code:
                raise OAuthFailure("authorization_cancelled")
            token = await asyncio.to_thread(oauth.exchange, provider, code, context["verifier"])
            token = await asyncio.to_thread(oauth.validate, provider, token)
            if "pair_device" in context:
                account = digest(token["team_id"] + ":" + token["user_id"])
                credential = secrets.token_urlsafe(48)
                device_id = secrets.token_hex(16)
                vault.put(
                    "device",
                    digest(credential),
                    {"account": account, "id": device_id},
                    ttl=90 * 86400,
                )
                vault.put(
                    "poll",
                    context["pair_device"],
                    {"state": "connected", "credential": credential},
                    ttl=300,
                )
            else:
                account = context["account"]
                if provider == "slack" and account != digest(
                    token["team_id"] + ":" + token["user_id"]
                ):
                    raise OAuthFailure("wrong_workspace_or_user")
            if provider == "slack":
                vault.put(
                    "slack-bot",
                    token["team_id"],
                    {
                        k: token[k]
                        for k in (
                            "access_token",
                            "refresh_token",
                            "expires_at",
                            "expires_in",
                            "scope",
                        )
                        if k in token
                    },
                )
            vault.put("integration", account + ":" + provider, token)
            if not vault.get("setup", account):
                vault.put(
                    "setup",
                    account,
                    {
                        "step": "connect",
                        "projects": [],
                        "resources": [],
                        "people": [],
                        "paused": True,
                    },
                )
            return page("Connected. Return to VirtualYou to continue.")
        except OAuthFailure as exc:
            if context.get("pair_device"):
                vault.put(
                    "poll", context["pair_device"], {"state": "denied", "error": exc.code}, ttl=300
                )
            return page(
                "Connection was not completed: "
                + exc.code.replace("_", " ")
                + ". Reconnect in the app."
            )

    @app.get("/v1/setup")
    async def setup(who=Depends(device)):
        account = who["account"]
        result = vault.get("setup", account) or {}
        integrations = []
        for provider in PROVIDERS:
            token = vault.get("integration", account + ":" + provider)
            integrations.append(
                {
                    "id": provider,
                    "connected": bool(token) and not token.get("error"),
                    "configured": oauth.configured(provider),
                    "label": token.get("label", "") if token else "",
                    "validated_at": token.get("validated_at") if token else None,
                    "error": token.get("error") if token else None,
                }
            )
        return dict(
            result,
            integrations=integrations,
            device_id=who["id"],
            model_ready=bool(settings.openai_key),
        )

    @app.get("/v1/resources/{provider}")
    async def resources(provider: str, parent: str = "", who=Depends(device)):
        result = await asyncio.to_thread(choices, oauth, who["account"], provider, parent)
        # Bind selection to options actually returned for this account; reject arbitrary IDs later.
        vault.put(
            "choices",
            who["account"] + ":" + provider + ":" + parent,
            [x["id"] for x in result],
            ttl=3600,
        )
        return result

    @app.post("/v1/setup/projects")
    async def select(payload: Selection, who=Depends(device)):
        async with account_locks[who["account"]]:
            import re

            if any(
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", p) for p in payload.projects
            ):
                raise ValueError()
            account = who["account"]
            for item in payload.resources:
                if (
                    set(item) != {"provider", "resource", "project"}
                    or item["project"] not in payload.projects
                    or item["provider"] not in {"github", "jira", "drive"}
                ):
                    raise ValueError()
                parent = item["resource"].split(":")[0] if item["provider"] == "jira" else ""
                allowed = (
                    vault.get("choices", account + ":" + item["provider"] + ":" + parent) or []
                )
                if item["resource"] not in allowed:
                    raise ValueError()
            allowed_people = vault.get("choices", account + ":slack:") or []
            if any(p not in allowed_people for p in payload.people):
                raise ValueError()
            previous = vault.get("setup", account) or {}
            for old in previous.get("resources", []):
                if old not in payload.resources:
                    await runtimes.drop_resource(account, old["provider"], old["resource"])
            vault.put(
                "setup", account, dict(previous, **payload.model_dump(), step="review", paused=True)
            )
            await runtimes.pause(account, True)
            await runtimes.configure_people(account, payload.people, payload.projects)
            return {"ok": True}

    @app.post("/v1/activities")
    async def activities(payload: Upload, who=Depends(device)):
        setup = vault.get("setup", who["account"]) or {}
        if payload.project not in setup.get("projects", []):
            raise HTTPException(403, "Project is not approved.")
        rows = []
        for record in payload.records:
            value = record.model_dump(mode="json")
            # Never accept raw paths, code patches, full prompts or tool I/O from a device.
            if value["source_path"] or value["diffs"] or value["prompts"] or value["tool_calls"]:
                raise HTTPException(422, "Upload sanitized summaries only.")
            value["session_id"] = digest(
                who["id"] + ":" + payload.project + ":" + value["session_id"]
            )
            value = redact_value(value)
            assert_safe_serialized(value)
            rows.append(value)
        await runtimes.upload(who["account"], payload.project, rows)
        return {"accepted": len(rows)}

    @app.post("/v1/refresh")
    async def refresh_route(who=Depends(device)):
        return await refresh(who["account"])

    @app.post("/v1/questions")
    async def question(payload: Question, who=Depends(device)):
        setup = vault.get("setup", who["account"]) or {}
        if not setup.get("projects"):
            raise HTTPException(409, "Select projects first.")
        draft = await runtimes.question(who["account"], payload.text, setup["projects"])
        vault.put("setup", who["account"], dict(setup, test_draft=draft["id"]))
        return draft

    @app.get("/v1/drafts")
    async def drafts(who=Depends(device)):
        return await runtimes.drafts(who["account"])

    @app.post("/v1/drafts/{draft_id}/decision")
    async def decision(draft_id: str, payload: Decision, who=Depends(device)):
        return await runtimes.decide(who["account"], draft_id, payload.revision, payload.approve)

    @app.get("/v1/people")
    async def people(who=Depends(device)):
        return await runtimes.people(who["account"])

    @app.post("/v1/people/{person}/enable")
    async def enable(person: str, payload: Enable, who=Depends(device)):
        setup = vault.get("setup", who["account"]) or {}
        if person not in setup.get("people", []):
            raise HTTPException(403)
        await runtimes.enable_person(who["account"], person, payload.version)
        return {"ok": True}

    @app.post("/v1/setup/complete")
    async def complete(who=Depends(device)):
        setup = vault.get("setup", who["account"]) or {}
        if not setup.get("projects"):
            raise HTTPException(409, "Choose projects.")
        token = await asyncio.to_thread(oauth.tokens, who["account"], "slack")
        if not token or not settings.openai_key:
            raise HTTPException(409, "Service setup is incomplete.")
        drafts = await runtimes.drafts(who["account"])
        if not any(
            d["id"] == setup.get("test_draft") and d["status"] == "delivered" for d in drafts
        ):
            raise HTTPException(409, "Approve and deliver your setup test first.")
        vault.put("setup", who["account"], dict(setup, step="complete", paused=False))
        await runtimes.pause(who["account"], False)
        return {"ok": True}

    @app.post("/v1/pause")
    async def pause(payload: Pause, who=Depends(device)):
        setup = vault.get("setup", who["account"]) or {}
        vault.put("setup", who["account"], dict(setup, paused=payload.paused))
        await runtimes.pause(who["account"], payload.paused)
        return {"ok": True}

    @app.delete("/v1/integrations/{provider}")
    async def disconnect(provider: str, who=Depends(device)):
        async with account_locks[who["account"]]:
            if provider not in PROVIDERS:
                raise HTTPException(404)
            if provider != "slack":
                await runtimes.revoke_source(who["account"], provider)
            setup = vault.get("setup", who["account"]) or {}
            vault.put(
                "setup",
                who["account"],
                dict(
                    setup,
                    resources=[r for r in setup.get("resources", []) if r["provider"] != provider],
                    paused=True,
                ),
            )
            await runtimes.pause(who["account"], True)
            vault.delete("integration", who["account"] + ":" + provider)
            if provider == "slack":
                await runtimes.stop(who["account"])
            return {
                "ok": True,
                "provider_grant_removal": "Manage remaining provider grants in your account security settings.",
            }

    @app.delete("/v1/device")
    def forget(auth: HTTPAuthorizationCredentials = Depends(bearer), who=Depends(device)):
        vault.delete("device", digest(auth.credentials))
        return {"ok": True}

    @app.post("/slack/events")
    async def events(request: Request):
        raw = await request.body()
        stamp = request.headers.get("x-slack-request-timestamp", "")
        if (
            not stamp.isdigit()
            or abs(time.time() - int(stamp)) > 300
            or not settings.signing_secret
        ):
            raise HTTPException(401)
        expected = (
            "v0="
            + hmac.new(
                settings.signing_secret.encode(),
                b"v0:" + stamp.encode() + b":" + raw,
                hashlib.sha256,
            ).hexdigest()
        )
        if not hmac.compare_digest(expected, request.headers.get("x-slack-signature", "")):
            raise HTTPException(401)
        if request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
            body = json.loads(parse_qs(raw.decode()).get("payload", ["{}"])[0])
            team = body.get("team", {}).get("id")
            user = body.get("user", {}).get("id")
            account = digest(str(team) + ":" + str(user))
            if not vault.get("integration", account + ":slack"):
                return {"ok": True}
            response = await runtimes.interaction(account, raw.decode())
            return Response(
                response.body, status_code=response.status, media_type="application/json"
            )
        body = json.loads(raw)
        if body.get("type") == "url_verification":
            return {"challenge": body["challenge"]}
        # Durable event id prevents Slack retries requeueing an already accepted event.
        event_id = body.get("event_id")
        if not event_id:
            return {"ok": True}
        for account in vault.keys("setup"):
            token = vault.get("integration", account + ":slack")
            if token and token["team_id"] == body.get("team_id"):
                key = digest(account + event_id)
                if not vault.get("seen", key):
                    vault.put("seen", key, {"accepted": True}, ttl=86400)
                    vault.put("event", key, {"account": account, "body": body}, ttl=86400)
        return {"ok": True}

    return app
