from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(".virtual-you"))
    activity_dir: Path | None = None
    api_key: str = ""
    provider: str = "demo"
    model: str = ""
    openai_api_key: str = ""
    ollama_url: str = "http://127.0.0.1:11434"
    heartbeat_seconds: float = 30
    heartbeat_enabled: bool = True
    activity_feed_url: str = ""
    activity_feed_token: str = ""
    stale_hours: float = 24
    live_delivery: bool = False
    slack_bot_token: str = ""
    slack_channels: tuple[str, ...] = ()
    discord_webhook_url: str = ""
    request_timeout: float = 60
    voice_model: str = "small"
    voice_language: str | None = None
    voice_provider: str = "local"
    elevenlabs_api_key: str = ""
    voice_test_mode: bool = False

    def prepare(self):
        self.data_dir = self.data_dir.expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data_dir.chmod(0o700)
        self.activity_dir = (
            (self.activity_dir or self.data_dir / "activities").expanduser().resolve()
        )
        self.activity_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.provider not in {"demo", "openai", "ollama"}:
            raise ValueError("VIRTUAL_YOU_LLM_PROVIDER must be demo, openai, or ollama")
        if self.voice_provider not in {"local", "elevenlabs"}:
            raise ValueError("VIRTUAL_YOU_VOICE_PROVIDER must be local or elevenlabs")
        if self.provider != "demo" and not self.model:
            raise ValueError("Set VIRTUAL_YOU_LLM_MODEL for the selected provider")
        if self.provider == "openai" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for the OpenAI provider")
        if self.heartbeat_seconds < 1 or self.stale_hours <= 0 or self.request_timeout <= 0:
            raise ValueError("Heartbeat, freshness, and timeout values must be positive")
        for url in (self.activity_feed_url, self.ollama_url):
            if not url:
                continue
            parsed = urlparse(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("Feed/model URLs must be HTTP(S) without embedded credentials")
            if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("Use HTTPS for non-local feed/model URLs")
        if self.discord_webhook_url:
            parsed = urlparse(self.discord_webhook_url)
            if (
                parsed.scheme != "https"
                or parsed.hostname != "discord.com"
                or not parsed.path.startswith("/api/webhooks/")
            ):
                raise ValueError("Use an official HTTPS Discord webhook URL")
        key_path = self.data_dir / "admin.key"
        if not self.api_key:
            try:
                fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                self.api_key = key_path.read_text().strip()
            else:
                self.api_key = secrets.token_urlsafe(32)
                with os.fdopen(fd, "w") as stream:
                    stream.write(self.api_key + "\n")
        if len(self.api_key) < 24:
            raise ValueError("Backend API key must have at least 24 characters")
        return self

    @classmethod
    def from_env(cls):
        def flag(name, default):
            value = os.getenv(name, str(default)).lower()
            if value not in {"true", "false", "1", "0"}:
                raise ValueError(f"{name} must be true or false")
            return value in {"true", "1"}

        return cls(
            data_dir=Path(os.getenv("VIRTUAL_YOU_DATA_DIR", ".virtual-you")),
            activity_dir=Path(os.environ["VIRTUAL_YOU_ACTIVITY_DIR"])
            if os.getenv("VIRTUAL_YOU_ACTIVITY_DIR")
            else None,
            api_key=os.getenv("VIRTUAL_YOU_API_KEY", ""),
            provider=os.getenv("VIRTUAL_YOU_LLM_PROVIDER", "demo"),
            model=os.getenv("VIRTUAL_YOU_LLM_MODEL", ""),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            ollama_url=os.getenv("VIRTUAL_YOU_OLLAMA_URL", "http://127.0.0.1:11434"),
            heartbeat_seconds=float(os.getenv("VIRTUAL_YOU_HEARTBEAT_SECONDS", "30")),
            heartbeat_enabled=flag("VIRTUAL_YOU_HEARTBEAT_ENABLED", True),
            activity_feed_url=os.getenv("VIRTUAL_YOU_ACTIVITY_FEED_URL", ""),
            activity_feed_token=os.getenv("VIRTUAL_YOU_ACTIVITY_FEED_TOKEN", ""),
            stale_hours=float(os.getenv("VIRTUAL_YOU_STALE_HOURS", "24")),
            live_delivery=flag("VIRTUAL_YOU_LIVE_DELIVERY", False),
            slack_bot_token=os.getenv("SLACK_BOT_TOKEN", ""),
            slack_channels=tuple(
                x.strip()
                for x in os.getenv("VIRTUAL_YOU_SLACK_CHANNELS", "").split(",")
                if x.strip()
            ),
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),
            request_timeout=float(os.getenv("VIRTUAL_YOU_REQUEST_TIMEOUT", "60")),
            voice_model=os.getenv("VIRTUAL_YOU_VOICE_MODEL", "small"),
            voice_language=os.getenv("VIRTUAL_YOU_VOICE_LANGUAGE") or None,
            voice_provider=os.getenv("VIRTUAL_YOU_VOICE_PROVIDER", "local"),
            elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY", ""),
        )
