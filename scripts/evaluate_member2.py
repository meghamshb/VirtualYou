"""Run paired model evaluations; --offline explicitly rehearses the pipeline without a model."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from pydantic import ValidationError

from virtual_you.backend.config import Settings
from virtual_you.backend.persona_eval import EvaluationCase, evaluate
from virtual_you.backend.soul import write_private
from virtual_you.contracts.reporting import PersonaSeed
from virtual_you.ingest.errors import IngestionError
from virtual_you.ingest.redact import redact_value

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline", action="store_true", help="Explicitly use the non-LLM demo provider"
    )
    parser.add_argument(
        "--activity", type=Path, help="Optional selected, normalized ActivityRecord; never raw logs"
    )
    parser.add_argument(
        "--manager-messages", type=Path, default=ROOT / "examples/persona/manager.messages.json"
    )
    parser.add_argument(
        "--teammate-messages", type=Path, default=ROOT / "examples/persona/teammate.messages.json"
    )
    args = parser.parse_args(argv)
    load_dotenv(ROOT / ".env")
    output = ROOT / ".virtual-you/member2-evaluation" / uuid4().hex[:12]
    try:
        settings = Settings.from_env()
        if args.offline:
            settings.provider, settings.model = "demo", ""
        elif settings.provider == "demo":
            raise ValueError("Configure a real provider, or explicitly pass --offline")
        settings.data_dir = output
        settings.activity_dir = None
        cases = [
            EvaluationCase.model_validate(redact_value(item))
            for item in json.loads((ROOT / "examples/persona/evaluation-cases.json").read_text())
        ]
        if args.activity:
            cases.append(
                EvaluationCase.model_validate(
                    {
                        "case_id": "selected-record",
                        "provenance": "user_supplied_normalized_record",
                        "request": {
                            "recipient_id": "manager",
                            "activity": redact_value(json.loads(args.activity.read_text())),
                        },
                    }
                )
            )
        seeds = [
            PersonaSeed(
                recipient_id=recipient,
                display_name=recipient.title(),
                messages=json.loads(path.read_text()),
            )
            for recipient, path in [
                ("manager", args.manager_messages),
                ("teammate", args.teammate_messages),
            ]
        ]
        result = asyncio.run(evaluate(cases, seeds, settings))
    except (ValueError, OSError, ValidationError, IngestionError):
        result = {
            "status": "blocked",
            "code": "configuration_or_input_required",
            "message": "Configure OpenAI/Ollama and valid local inputs, or explicitly use --offline. No model success is claimed.",
            "human_acceptance": "pending",
        }
        write_private(output / "evaluation.json", json.dumps(result, indent=2))
    print(
        json.dumps(
            {
                "status": result["status"],
                "provider": result.get("provider"),
                "completed_pairs": len(result.get("cases", [])),
                "output": str(output),
                "human_acceptance": result["human_acceptance"],
            },
            indent=2,
        )
    )
    if result["status"] in {"blocked", "generation_failed"}:
        return 1
    return 2 if result["status"] == "review_flags" else 0


if __name__ == "__main__":
    sys.exit(main())
