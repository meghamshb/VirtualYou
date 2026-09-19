from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone

from dotenv import load_dotenv

from virtual_you.backend.config import Settings


def main():
    parser = argparse.ArgumentParser(description="Virtual You backend (one worker, local owner)")
    parser.add_argument(
        "command", nargs="?", choices=["serve", "seed-demo", "show-key"], default="serve"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--voice-test",
        action="store_true",
        help="Open the local ElevenLabs response tester without a login (loopback only).",
    )
    args = parser.parse_args()
    if args.voice_test and (
        args.command != "serve" or args.host not in {"127.0.0.1", "::1", "localhost"}
    ):
        parser.error(
            "--voice-test requires serve with a loopback host (127.0.0.1, ::1 or localhost)."
        )
    load_dotenv()
    settings = Settings.from_env().prepare()
    if args.voice_test:
        settings.voice_test_mode = True
        settings.voice_provider = "elevenlabs"
    if args.command == "show-key":
        print(settings.api_key)
    elif args.command == "seed-demo":
        asyncio.run(seed_demo(settings))
    else:
        import uvicorn

        from virtual_you.backend.app import create_app

        host = "[::1]" if args.host == "::1" else args.host
        if args.voice_test:
            print(f"ElevenLabs audio test: http://{host}:{args.port}/ — no backend login needed.")
        print(
            f"Review UI: http://{host}:{args.port}/review — use `virtual-you-server show-key` to sign in."
        )
        uvicorn.run(
            create_app(settings),
            host=args.host,
            port=args.port,
            workers=1,
            proxy_headers=not args.voice_test,
        )


async def seed_demo(settings):
    from virtual_you.backend.persona import PersonaService
    from virtual_you.backend.providers import DemoProvider
    from virtual_you.backend.store import Store
    from virtual_you.contracts.activity import ActivityRecord
    from virtual_you.contracts.reporting import PersonaSeed
    from virtual_you.ingest.store import ActivityRecordRepository

    now = datetime.now(timezone.utc)
    record = ActivityRecord(
        session_id="synthetic-demo",
        source="claude",
        start_state="The demo payment callback accepted unvalidated payloads.",
        prompts=["Add validation to the demo payment callback."],
        reasoning_summary="Recorded explanation: validate incoming payloads before updating a payment.",
        files_changed=[{"path": "src/payments/callback.py", "operation": "modified"}],
        tool_calls=[
            {
                "call_id": "demo-test",
                "name": "pytest",
                "status": "succeeded",
                "result_summary": "3 passed",
            }
        ],
        end_state="Demo callback validation was added; its three tests passed.",
        timestamp_range={"started_at": now, "ended_at": now},
    )
    ActivityRecordRepository(settings.activity_dir).save(record)
    store = Store(settings.data_dir / "backend.sqlite3")
    service = PersonaService(settings, store, DemoProvider())
    for recipient, greeting, closing in [
        ("manager", "Dear colleague", "Regards"),
        ("teammate", "Hey team!", "Cheers"),
    ]:
        await service.create(
            PersonaSeed(
                recipient_id=recipient,
                display_name=recipient.title(),
                messages=[
                    f"{greeting}, sample update {i}: I reviewed the task and noted the next step. {closing}."
                    for i in range(1, 11)
                ],
            )
        )
    print(
        json.dumps(
            {
                "seeded": "synthetic-demo",
                "personas": ["manager", "teammate"],
                "delivery": "No messages sent",
            }
        )
    )


if __name__ == "__main__":
    main()
