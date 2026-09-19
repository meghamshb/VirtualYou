"""Member 2 local tools: python -m virtual_you.persona --help."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from virtual_you.backend.errors import ServiceError
from virtual_you.contracts.reporting import PersonaProfile, PersonaSeed, PromptRequest
from virtual_you.ingest.errors import IngestionError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Local recipient profiles and prompt handoff")
    parser.add_argument(
        "--data-dir", type=Path, help="Private storage; defaults to VIRTUAL_YOU_DATA_DIR"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Extract style from a JSON array of 10–20 messages")
    create.add_argument("--messages", required=True, type=Path)
    create.add_argument("--recipient", required=True)
    create.add_argument("--name", required=True)
    assemble = commands.add_parser(
        "assemble", help="Save an AssembledPrompt without calling a model"
    )
    assemble.add_argument("--activity", required=True, type=Path)
    assemble.add_argument("--recipient", required=True)
    assemble.add_argument(
        "--soul", type=Path, help="Exported soul.md; otherwise use the local profile store"
    )
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except ServiceError as error:
        print(
            json.dumps({"error": {"code": error.code, "message": error.message}}), file=sys.stderr
        )
        return 1
    except (OSError, ValueError, ValidationError, IngestionError):
        # Never echo pasted messages, file contents, provider responses or secrets on errors.
        print(
            json.dumps(
                {
                    "error": {
                        "code": "invalid_input",
                        "message": "Check local files and configuration; input must match the contract.",
                    }
                }
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, indent=2))
    return 0


async def _run(args):
    import httpx
    from dotenv import load_dotenv

    from virtual_you.backend.config import Settings
    from virtual_you.backend.persona import PersonaService
    from virtual_you.backend.prompts import assemble_activity_prompt
    from virtual_you.backend.providers import make_provider
    from virtual_you.backend.soul import profile_from_soul, write_private
    from virtual_you.backend.store import Store

    load_dotenv()
    settings = Settings.from_env()
    if args.data_dir:
        settings.data_dir = args.data_dir
    # Assembly is entirely local and must not require model credentials.
    if args.command == "assemble":
        settings.provider, settings.model = "demo", ""
    settings.prepare()
    store = Store(settings.data_dir / "backend.sqlite3")
    recipient_hash = hashlib.sha256(args.recipient.encode()).hexdigest()[:24]
    if args.command == "create":
        seed = PersonaSeed(
            recipient_id=args.recipient,
            display_name=args.name,
            messages=json.loads(args.messages.read_text(encoding="utf-8")),
        )
        async with httpx.AsyncClient(
            timeout=settings.request_timeout, follow_redirects=False
        ) as client:
            profile = await PersonaService(settings, store, make_provider(settings, client)).create(
                seed
            )
        return {
            "recipient_id": profile.recipient_id,
            "version": profile.version,
            "provider": profile.provider,
            "soul": str(settings.data_dir / "personas" / recipient_hash / "soul.md"),
        }
    request = PromptRequest(
        recipient_id=args.recipient, activity=json.loads(args.activity.read_text(encoding="utf-8"))
    )
    profile = (
        profile_from_soul(args.soul.read_text(encoding="utf-8"), recipient_id=args.recipient)
        if args.soul
        else PersonaProfile.model_validate(store.get_persona(args.recipient))
    )
    prompt = assemble_activity_prompt(profile, request.activity, recipient_id=args.recipient)
    output = settings.data_dir / "prompts" / (recipient_hash + ".json")
    write_private(output, prompt.model_dump_json(indent=2))
    return {
        "recipient_id": args.recipient,
        "persona_version": prompt.persona_version,
        "evidence_count": len(prompt.evidence),
        "prompt": str(output),
    }


if __name__ == "__main__":
    raise SystemExit(main())
