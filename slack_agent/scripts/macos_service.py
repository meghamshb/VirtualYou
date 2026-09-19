"""Install once after configuring .env; run headlessly at login with launchd."""

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

LABEL = "local.virtualyou.agent"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["install", "status", "uninstall"])
    parser.add_argument("--mode", choices=["socket", "oauth"], default="socket")
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise SystemExit(
            "This helper is for macOS. Use a process supervisor on your hosted server."
        )
    root = Path(__file__).resolve().parents[1]
    path = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
    domain = f"gui/{os.getuid()}"
    if args.action == "status":
        subprocess.run(["launchctl", "print", f"{domain}/{LABEL}"], check=False)
        return
    if args.action == "uninstall":
        subprocess.run(["launchctl", "bootout", domain, str(path)], check=False)
        path.unlink(missing_ok=True)
        print("VirtualYou login service removed. Saved setup and drafts were kept.")
        return
    saved = dotenv_values(root / ".env")
    required = ["VIRTUAL_YOU_SLACK_OWNER", "VIRTUAL_YOU_SLACK_TEAM"]
    required += (
        ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"]
        if args.mode == "socket"
        else [
            "SLACK_CLIENT_ID",
            "SLACK_CLIENT_SECRET",
            "SLACK_SIGNING_SECRET",
            "SLACK_REDIRECT_URI",
        ]
    )
    missing = [name for name in required if not saved.get(name)]
    if missing:
        raise SystemExit("Finish one-time .env setup first: " + ", ".join(missing))
    if not (root / ".env").exists():
        raise SystemExit(
            "Save settings in .env first; launchd does not inherit this terminal environment."
        )
    os.chmod(root / ".env", 0o600)
    logs = root / ".virtual-you" / "service-logs"
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": LABEL,
        "ProgramArguments": [
            str(root / ".venv" / "bin" / "python"),
            str(root / ("app.py" if args.mode == "socket" else "app_oauth.py")),
        ],
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "StandardOutPath": str(logs / "stdout.log"),
        "StandardErrorPath": str(logs / "stderr.log"),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    }
    with path.open("wb") as stream:
        plistlib.dump(payload, stream)
    path.chmod(0o600)
    subprocess.run(["launchctl", "bootout", domain, str(path)], check=False, capture_output=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)
    print(
        "Installed VirtualYou at login. Secrets are read from .env, not stored in the launch plist."
    )


if __name__ == "__main__":
    main()
