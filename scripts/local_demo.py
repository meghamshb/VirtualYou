"""Run an isolated local demo without loading .env or contacting providers."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


async def initialize_demo(settings):
    from virtual_you.backend.app import create_app
    from virtual_you.backend.cli import seed_demo
    from virtual_you.contracts.reporting import DraftRequest

    await seed_demo(settings)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        await app.state.workflow.create(
            DraftRequest(
                recipient_id="manager",
                destination={"target": "demo-channel"},
                question="Summarize this synthetic demo activity for review.",
            ),
            draft_id="synthetic-demo-report",
        )


async def check_app(app, settings):
    import httpx

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://local-demo",
            headers={"Authorization": "Bearer " + settings.api_key},
        ) as client:
            status = await client.get("/api/status")
            status.raise_for_status()
            assert status.json()["provider"] == "demo:extractive"
            assert status.json()["delivery_mode"] == "simulation"
            assert status.json()["activity"]["count"] > 0
            draft = await client.post(
                "/api/drafts",
                json={"recipient_id": "manager", "destination": {"target": "demo-channel"}},
            )
            draft.raise_for_status()
            value = draft.json()
            decision = await client.post(
                f"/api/drafts/{value['id']}/decision",
                json={"expected_revision": value["revision"], "action": "approve"},
            )
            decision.raise_for_status()
            receipt = await client.post(
                f"/api/drafts/{value['id']}/deliver",
                json={"expected_revision": value["revision"]},
            )
            receipt.raise_for_status()
            assert receipt.json()["status"] == "simulated"
    print("PASS: local startup, indexed activity, draft, approval, simulated delivery.")
    print("No external provider was contacted and no message was sent.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".virtual-you" / "portable-demo",
    )
    parser.add_argument(
        "--check", action="store_true", help="Run a local API smoke check and exit."
    )
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error("Python 3.11 or newer is required. Use your installed python3 interpreter.")
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port between 1024 and 65535.")
    # Optional adapters also inspect process environment. Clear provider config
    # in this process only; never read or rewrite the user's .env files.
    integration_prefixes = (
        "VIRTUAL_YOU_",
        "SLACK_",
        "OPENAI_",
        "ELEVENLABS_",
        "GITHUB_",
        "JIRA_",
        "GOOGLE_",
        "ATLASSIAN_",
        "DISCORD_",
    )
    for name in list(os.environ):
        if name.startswith(integration_prefixes) or name in {"GH_TOKEN", "GH_ENTERPRISE_TOKEN"}:
            os.environ.pop(name)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    for flag in ("VIRTUAL_YOU_MCP_JIRA", "VIRTUAL_YOU_MCP_DRIVE", "VIRTUAL_YOU_MCP_GITHUB"):
        os.environ[flag] = "false"
    try:
        import uvicorn

        from virtual_you.backend.app import create_app
        from virtual_you.backend.config import Settings
    except ImportError as error:
        parser.error(
            f"Backend dependencies are missing ({error.name}). From the repository root, run "
            "python -m pip install -e '.[backend]' in your activated virtual environment."
        )
    data_dir = args.data_dir.expanduser().resolve()
    marker = data_dir / "portable-demo.json"
    if (data_dir / "backend.sqlite3").exists() and not marker.exists():
        parser.error(
            "This folder contains an existing backend. Choose a fresh --data-dir for demo data."
        )
    settings = Settings(
        data_dir=data_dir,
        provider="demo",
        live_delivery=False,
        heartbeat_enabled=False,
        stale_hours=24 * 365,
    ).prepare()
    if not marker.exists():
        asyncio.run(initialize_demo(settings))
        marker.write_text(json.dumps({"format": 1, "synthetic": True}) + "\n")
        marker.chmod(0o600)
    app = create_app(settings)
    if args.check:
        asyncio.run(check_app(app, settings))
        return
    print(f"Local demo: http://127.0.0.1:{args.port}/review", flush=True)
    print(f"Electron: connect port {args.port}, then choose this data folder:", flush=True)
    print(data_dir, flush=True)
    print(
        "Demo provider; synthetic evidence; delivery is simulated. No API keys required.",
        flush=True,
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
