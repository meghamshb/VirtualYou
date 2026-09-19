"""Fixed provider endpoints, verified identities, scopes and serialized token rotation."""

import base64
import hashlib
import secrets
import threading
import time
from urllib.parse import urlencode

import httpx

BOT_SCOPES = "app_mentions:read,chat:write,im:history,im:read,im:write,users:read,assistant:write,commands,files:read"
USER_SCOPES = "im:read,im:history,users:read,chat:write,channels:read,channels:history,groups:read,groups:history,mpim:read,mpim:history"
PROVIDERS = {
    "slack": (
        "https://slack.com/oauth/v2/authorize",
        "https://slack.com/api/oauth.v2.access",
        BOT_SCOPES,
    ),
    "github": (
        "https://github.com/login/oauth/authorize",
        "https://github.com/login/oauth/access_token",
        "repo",
    ),
    "jira": (
        "https://auth.atlassian.com/authorize",
        "https://auth.atlassian.com/oauth/token",
        "read:jira-work read:jira-user offline_access",
    ),
    "drive": (
        "https://accounts.google.com/o/oauth2/v2/auth",
        "https://oauth2.googleapis.com/token",
        "https://www.googleapis.com/auth/drive.readonly",
    ),
}


class OAuthFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class OAuth:
    def __init__(self, settings, vault, transport=None):
        self.settings, self.vault = settings, vault
        self.http = httpx.Client(timeout=20, follow_redirects=False, transport=transport)
        self.lock = threading.RLock()

    def configured(self, provider):
        return provider in PROVIDERS and all(self.settings.clients.get(provider, ("", "")))

    def callback(self, provider):
        return f"{self.settings.base_url}/oauth/{provider}/callback"

    def start(self, provider, context, cookie):
        if not self.configured(provider):
            raise OAuthFailure("service_not_configured")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        from .store import digest

        self.vault.put(
            "oauth",
            digest(state),
            dict(context, provider=provider, cookie=digest(cookie), verifier=verifier),
            ttl=600,
        )
        fields = dict(
            client_id=self.settings.clients[provider][0],
            state=state,
            redirect_uri=self.callback(provider),
            response_type="code",
            scope=PROVIDERS[provider][2],
        )
        if provider == "slack":
            fields["user_scope"] = USER_SCOPES
        if provider in ("github", "drive"):
            fields.update(
                code_challenge_method="S256",
                code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode(),
            )
        if provider == "jira":
            fields.update(audience="api.atlassian.com", prompt="consent")
        if provider == "drive":
            fields.update(access_type="offline", prompt="consent")
        return PROVIDERS[provider][0] + "?" + urlencode(fields)

    def json(self, method, url, **kwargs):
        try:
            r = self.http.request(method, url, **kwargs)
            r.raise_for_status()
            result = r.json()
            if isinstance(result, dict) and (result.get("error") or result.get("ok") is False):
                raise ValueError()
            return result
        except (httpx.HTTPError, ValueError) as exc:
            raise OAuthFailure("authorization_failed_reconnect") from exc

    def exchange(self, provider, code, verifier):
        fields = dict(
            client_id=self.settings.clients[provider][0],
            client_secret=self.settings.clients[provider][1],
            code=code,
            redirect_uri=self.callback(provider),
            grant_type="authorization_code",
        )
        if provider in ("github", "drive"):
            fields["code_verifier"] = verifier
        return self.json(
            "POST",
            PROVIDERS[provider][1],
            headers={"Accept": "application/json"},
            **({"json": fields} if provider == "jira" else {"data": fields}),
        )

    def validate(self, provider, token):
        access = token.get("access_token", "")
        if not access:
            raise OAuthFailure("missing_access_token")
        headers = {"Authorization": "Bearer " + access, "Accept": "application/json"}
        granted = set(str(token.get("scope", "")).replace(",", " ").split())
        if provider == "slack":
            user = token.get("authed_user", {})
            if not set(USER_SCOPES.split(",")).issubset(set(user.get("scope", "").split(","))):
                raise OAuthFailure("missing_user_scopes")
            if not set(BOT_SCOPES.split(",")).issubset(granted):
                raise OAuthFailure("missing_bot_scopes")
            bot = self.json("POST", "https://slack.com/api/auth.test", headers=headers)
            identity = self.json(
                "POST",
                "https://slack.com/api/auth.test",
                headers={"Authorization": "Bearer " + user.get("access_token", "")},
            )
            team = token.get("team", {}).get("id")
            if (
                not team
                or bot.get("team_id") != team
                or identity.get("team_id") != team
                or identity.get("user_id") != user.get("id")
            ):
                raise OAuthFailure("wrong_workspace")
            token.update(team_id=team, user_id=user["id"], label=identity.get("user", user["id"]))
        elif provider == "github":
            if "repo" not in granted:
                raise OAuthFailure("missing_scopes")
            who = self.json("GET", "https://api.github.com/user", headers=headers)
            token.update(label=who["login"], user_id=str(who["id"]))
        elif provider == "jira":
            sites = self.json(
                "GET", "https://api.atlassian.com/oauth/token/accessible-resources", headers=headers
            )
            sites = [
                x
                for x in sites
                if {"read:jira-work", "read:jira-user"}.issubset(set(x.get("scopes", [])))
            ]
            if not sites:
                raise OAuthFailure("missing_scopes")
            token.update(
                label="Choose a Jira site",
                sites=[{"id": x["id"], "name": x["name"]} for x in sites],
            )
        else:
            if PROVIDERS["drive"][2] not in granted:
                raise OAuthFailure("missing_scopes")
            who = self.json(
                "GET",
                "https://www.googleapis.com/drive/v3/about",
                headers=headers,
                params={"fields": "user(displayName,emailAddress)"},
            )
            token["label"] = who["user"].get("displayName", "Google Drive")
        token["validated_at"] = time.time()
        for value in [token, token.get("authed_user", {})]:
            if value.get("expires_in"):
                value["expires_at"] = time.time() + int(value["expires_in"])
        return token

    def tokens(self, account, provider):
        with self.lock:
            key = account + ":" + provider
            token = self.vault.get("integration", key)
            if not token:
                raise OAuthFailure("not_connected")
            if provider == "slack":
                shared = self.vault.get("slack-bot", token["team_id"])
                if shared:
                    for field in (
                        "access_token",
                        "refresh_token",
                        "expires_at",
                        "expires_in",
                        "scope",
                    ):
                        if field in shared:
                            token[field] = shared[field]
            try:
                for target in [token, token.get("authed_user", {})]:
                    if not target or target.get("expires_at", float("inf")) > time.time() + 120:
                        continue
                    if not target.get("refresh_token"):
                        raise OAuthFailure("expired_reconnect")
                    fields = dict(
                        grant_type="refresh_token",
                        refresh_token=target["refresh_token"],
                        client_id=self.settings.clients[provider][0],
                        client_secret=self.settings.clients[provider][1],
                    )
                    renewed = self.json(
                        "POST",
                        PROVIDERS[provider][1],
                        **({"json": fields} if provider == "jira" else {"data": fields}),
                    )
                    target.update(renewed)
                    target["expires_at"] = time.time() + int(renewed.get("expires_in", 3600))
                    self.vault.put("integration", key, token)
                    if provider == "slack" and target is token:
                        self.vault.put(
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
            except OAuthFailure:
                token["error"] = "expired_reconnect"
                self.vault.put("integration", key, token)
                raise
            return token
