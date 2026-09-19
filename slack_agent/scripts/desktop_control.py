"""Secret-free commands used by the local macOS companion."""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from dotenv import dotenv_values


def install_url(settings):
    value = settings.get("SLACK_REDIRECT_URI", "")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("The app operator must configure the HTTPS Slack OAuth callback first.")
    return urlunparse((parsed.scheme, parsed.netloc, "/slack/install", "", "", ""))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["connect", "status", "start", "stop", "slack"])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    settings = dotenv_values(root / ".env")
    if args.action == "connect":
        subprocess.run(["open", install_url(settings)], check=True)
    elif args.action == "slack":
        subprocess.run(["open", "slack://open"], check=True)
    elif args.action in {"start", "stop"}:
        subprocess.run(
            [
                sys.executable,
                str(root / "scripts/macos_service.py"),
                "install" if args.action == "start" else "uninstall",
                "--mode",
                "oauth",
            ],
            check=True,
        )
    else:
        required = [
            "SLACK_CLIENT_ID",
            "SLACK_CLIENT_SECRET",
            "SLACK_SIGNING_SECRET",
            "SLACK_REDIRECT_URI",
            "VIRTUAL_YOU_SLACK_OWNER",
            "VIRTUAL_YOU_SLACK_TEAM",
        ]
        configured = all(settings.get(k) for k in required)
        result = subprocess.run(
            ["launchctl", "print", f"gui/{__import__('os').getuid()}/local.virtualyou.agent"],
            capture_output=True,
        )
        print(
            json.dumps(
                {
                    "configured": configured,
                    "service_loaded": result.returncode == 0,
                    "message": "Setup configured. Connect Slack once, then use VirtualYou Home for sources, people, and style review."
                    if configured
                    else "App operator setup is incomplete. Configure the OAuth app credentials and HTTPS callback in .env. No user tokens are needed.",
                }
            )
        )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.CalledProcessError):
        print(
            "Could not complete this action. Check the operator setup and service logs.",
            file=sys.stderr,
        )
        raise SystemExit(1)
