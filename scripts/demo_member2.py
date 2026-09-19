"""Reproducible Member 2 acceptance demo. Synthetic data; no external model or messages."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from virtual_you.backend.config import Settings
from virtual_you.backend.drafting import DraftEngine
from virtual_you.backend.persona import PersonaService
from virtual_you.backend.prompts import assemble_activity_prompt
from virtual_you.backend.providers import DemoProvider
from virtual_you.backend.retrieval import RetrievalService
from virtual_you.backend.soul import profile_from_soul, write_private
from virtual_you.backend.store import Store
from virtual_you.contracts.activity import ActivityRecord
from virtual_you.contracts.reporting import Destination, DraftRequest, PersonaSeed

ROOT = Path(__file__).resolve().parents[1]


async def demo(data_dir: Path) -> dict:
    settings = Settings(data_dir=data_dir, heartbeat_enabled=False).prepare()
    store = Store(settings.data_dir / "backend.sqlite3")
    provider = DemoProvider()
    service = PersonaService(settings, store, provider)
    record = ActivityRecord.model_validate_json(
        (ROOT / "tests/fixtures/activity_record.json").read_text()
    )
    retrieval = RetrievalService(store)
    retrieval.upsert(record.model_dump(mode="json"))
    engine = DraftEngine(settings, store, retrieval, provider)
    prompts, reports, texts = [], [], []
    for recipient in ("manager", "teammate"):
        messages = json.loads((ROOT / f"examples/persona/{recipient}.messages.json").read_text())
        profile = await service.create(
            PersonaSeed(recipient_id=recipient, display_name=recipient.title(), messages=messages)
        )
        # Exercise the actual local Markdown handoff, not just the in-memory object.
        loaded = profile_from_soul(profile.soul_md, recipient_id=recipient)
        prompt = assemble_activity_prompt(loaded, record, recipient_id=recipient)
        draft = await engine.generate(
            DraftRequest(recipient_id=recipient, destination=Destination(target="not-sent"))
        )
        prompts.append(prompt)
        reports.append(draft["report"])
        texts.append(draft["text"])
        for name, content in [
            ("soul.md", profile.soul_md),
            ("prompt.json", prompt.model_dump_json(indent=2)),
            ("preview.txt", draft["text"]),
            ("report.json", json.dumps(draft["report"], indent=2)),
        ]:
            write_private(settings.data_dir / "demo" / recipient / name, content)
    assert prompts[0].system == prompts[1].system
    assert prompts[0].evidence == prompts[1].evidence
    assert reports[0] == reports[1]
    assert texts[0] != texts[1]
    # No contaminated fact from either historical example set reaches the preview.
    for marker in ("Atlas", "Orbit", "18 milliseconds", "99 percent", "Friday"):
        assert all(marker not in text for text in texts)
    result = {
        "provider": provider.name,
        "synthetic": True,
        "recipients": ["manager", "teammate"],
        "messages_per_recipient": 10,
        "examples_per_profile": 5,
        "same_evidence": True,
        "same_factual_report": True,
        "different_style": True,
        "outbound_messages": 0,
        "output": str(settings.data_dir / "demo"),
    }
    write_private(settings.data_dir / "demo" / "acceptance.json", json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".virtual-you/member2-demo")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(demo(args.data_dir)), indent=2))
