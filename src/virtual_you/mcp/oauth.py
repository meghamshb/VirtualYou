"""GitHub OAuth: intern clicks Authorize GitHub; token never enters ActivityRecord."""

from __future__ import annotations

import json
import os
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable, Optional, Union

try:
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
except ImportError:
    HTMLResponse = None
    JSONResponse = None
    RedirectResponse = None


PathLike = Union[str, Path]

CLIENT_ID_ENV = "GITHUB_CLIENT_ID"
CLIENT_SECRET_ENV = "GITHUB_CLIENT_SECRET"
REDIRECT_ENV = "GITHUB_OAUTH_REDIRECT"
SCOPES_ENV = "GITHUB_OAUTH_SCOPES"
TOKEN_NAME = "github-oauth.json"
STATE_NAME = "github-oauth-state"
DEFAULT_REDIRECT = "http://127.0.0.1:8765/github/callback"
DEFAULT_SCOPES = "public_repo"
AUTHORIZE_ENDPOINT = "https://github.com/login/oauth/authorize"
TOKEN_ENDPOINT = "https://github.com/login/oauth/access_token"
USER_ENDPOINT = "https://api.github.com/user"
CALLBACK_PATH = "/github/callback"


class OAuthError(RuntimeError):
    """User-facing OAuth failure; message is safe to print."""


def load_token(data_directory: PathLike) -> str:
    path = Path(data_directory) / TOKEN_NAME
    if not path.exists():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("access_token") or "").strip()


def load_identity(data_directory: PathLike) -> dict:
    path = Path(data_directory) / TOKEN_NAME
    if not path.exists():
        return {"connected": False, "login": ""}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"connected": False, "login": ""}
    token = str(payload.get("access_token") or "").strip()
    return {
        "connected": bool(token),
        "login": str(payload.get("login") or ""),
    }


def save_token(data_directory: PathLike, payload: dict) -> Path:
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise OAuthError("GitHub did not return an access token.")
    path = Path(data_directory) / TOKEN_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "access_token": token,
                "token_type": str(payload.get("token_type") or "bearer"),
                "scope": str(payload.get("scope") or ""),
                "login": str(payload.get("login") or ""),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    return path


def authorize_url(state: str, environ=None) -> str:
    env = environ if environ is not None else os.environ
    client_id = (env.get(CLIENT_ID_ENV) or "").strip()
    if not client_id:
        raise OAuthError("Set GITHUB_CLIENT_ID for Authorize GitHub.")
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri(env),
            "scope": (env.get(SCOPES_ENV) or DEFAULT_SCOPES).strip(),
            "state": state,
            "allow_signup": "false",
        }
    )
    return AUTHORIZE_ENDPOINT + "?" + query


def redirect_uri(environ=None) -> str:
    env = environ if environ is not None else os.environ
    return (env.get(REDIRECT_ENV) or DEFAULT_REDIRECT).strip() or DEFAULT_REDIRECT


def new_state(data_directory: PathLike) -> str:
    value = secrets.token_urlsafe(32)
    path = Path(data_directory) / STATE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    os.chmod(path, 0o600)
    return value


def consume_state(data_directory: PathLike, received: str) -> bool:
    path = Path(data_directory) / STATE_NAME
    if not path.exists() or not received:
        return False
    expected = path.read_text(encoding="utf-8").strip()
    try:
        path.unlink()
    except OSError:
        pass
    return bool(expected) and secrets.compare_digest(expected, received)


def exchange_code(
    code: str,
    environ=None,
    *,
    http_post: Optional[Callable] = None,
    http_get: Optional[Callable] = None,
) -> dict:
    env = environ if environ is not None else os.environ
    client_id = (env.get(CLIENT_ID_ENV) or "").strip()
    client_secret = (env.get(CLIENT_SECRET_ENV) or "").strip()
    if not client_id or not client_secret:
        raise OAuthError("Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET.")
    if not code:
        raise OAuthError("GitHub did not return an authorization code.")
    poster = http_post or _form_post
    token_payload = poster(
        TOKEN_ENDPOINT,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri(env),
        },
    )
    if token_payload.get("error"):
        raise OAuthError("GitHub authorization was denied or expired.")
    getter = http_get or _json_get
    identity = getter(
        USER_ENDPOINT,
        "Bearer {}".format(token_payload.get("access_token") or ""),
    )
    token_payload["login"] = str((identity or {}).get("login") or "")
    return token_payload


def complete_callback(data_directory: PathLike, code: str, state: str, environ=None) -> dict:
    if not consume_state(data_directory, state):
        raise OAuthError("GitHub authorization state did not match. Try Authorize GitHub again.")
    payload = exchange_code(code, environ)
    save_token(data_directory, payload)
    return load_identity(data_directory)


def run_local_authorize(
    data_directory: PathLike,
    environ=None,
    *,
    open_browser: Optional[Callable[[str], object]] = webbrowser.open,
    wait_for_code: Optional[Callable] = None,
) -> dict:
    """Open GitHub's Authorize page and store the token under the data directory."""

    env = environ if environ is not None else os.environ
    state = new_state(data_directory)
    url = authorize_url(state, env)
    parsed = urllib.parse.urlparse(redirect_uri(env))
    if parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.path != CALLBACK_PATH:
        raise OAuthError(
            "CLI Authorize GitHub requires GITHUB_OAUTH_REDIRECT "
            "http://127.0.0.1:8765/github/callback (or use the review UI)."
        )
    if open_browser is not None:
        open_browser(url)
    waiter = wait_for_code or _wait_localhost_callback
    code, returned_state = waiter(parsed.hostname, parsed.port or 8765)
    return complete_callback(data_directory, code, returned_state or state, env)


def attach_oauth_routes(app, data_directory: PathLike) -> None:
    """Public Authorize GitHub routes on the review UI. Token is never returned."""

    if RedirectResponse is None:
        return
    root = Path(data_directory)

    @app.get("/github/status")
    def github_status():
        body = load_identity(root)
        body["repo"] = (os.environ.get("VIRTUAL_YOU_GITHUB_REPO") or "").strip()
        return body

    @app.get("/github/authorize")
    def github_authorize():
        try:
            state = new_state(root)
            return RedirectResponse(authorize_url(state), status_code=302)
        except OAuthError as error:
            return JSONResponse({"error": str(error)}, status_code=503)

    @app.get("/github/callback")
    def github_callback(code: str = "", state: str = "", error: str = ""):
        if error:
            return _html_page("GitHub authorization was cancelled.", status_code=400)
        try:
            identity = complete_callback(root, code, state)
        except OAuthError as exc:
            return _html_page(str(exc), status_code=400)
        login = identity.get("login") or "your GitHub account"
        return _html_page(
            "GitHub connected as {}. You can close this tab. "
            "CI, PR, and review questions run on ingest — you do not run github-ask.".format(
                login
            )
        )


def _html_page(message: str, status_code: int = 200):
    markup = (
        "<!doctype html><html><body><p>{}</p></body></html>".format(
            message.replace("<", "&lt;")
        )
    )
    if HTMLResponse is None:
        return markup
    return HTMLResponse(markup, status_code=status_code)


def _wait_localhost_callback(host: str, port: int) -> tuple:
    box = {"code": "", "state": "", "error": ""}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != CALLBACK_PATH:
                self.send_error(404)
                return
            query = urllib.parse.parse_qs(parsed.query)
            box["code"] = (query.get("code") or [""])[0]
            box["state"] = (query.get("state") or [""])[0]
            box["error"] = (query.get("error") or [""])[0]
            ok = bool(box["code"]) and not box["error"]
            body = (
                b"GitHub connected. You can close this tab."
                if ok
                else b"GitHub authorization failed. Return to the terminal."
            )
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args) -> None:
            return

    server = HTTPServer((host, port), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout=300)
    server.server_close()
    if box["error"] or not box["code"]:
        raise OAuthError("GitHub authorization did not complete.")
    return box["code"], box["state"]


def _form_post(url: str, fields: dict) -> dict:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    return _read_json(request)


def _json_get(url: str, authorization: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": authorization,
        },
        method="GET",
    )
    return _read_json(request)


def _read_json(request: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as error:
        raise OAuthError("Could not reach GitHub to finish authorization.") from error
    if not isinstance(payload, dict):
        raise OAuthError("GitHub returned an unexpected authorization payload.")
    return payload
