"""Paired persona acceptance evidence, reusing the real profile and draft services.

Automatic checks are diagnostics. They never certify semantic accuracy or send messages.
"""

from __future__ import annotations

import json
import re

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from virtual_you.backend.delivery import DeliveryGateway
from virtual_you.backend.drafting import DraftEngine
from virtual_you.backend.errors import ServiceError
from virtual_you.backend.persona import PersonaService
from virtual_you.backend.providers import UNKNOWN, make_provider
from virtual_you.backend.retrieval import RetrievalService, canonical_hash
from virtual_you.backend.soul import profile_from_soul, write_private
from virtual_you.backend.store import Store
from virtual_you.backend.workflow import Workflow
from virtual_you.contracts.reporting import (
    SECTION_TITLES,
    Destination,
    DraftRequest,
    PromptRequest,
    RetrievalRequest,
)
from virtual_you.ingest.redact import redact_value


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^[a-z0-9-]+$", max_length=80)
    provenance: str = "synthetic_fixture"
    request: PromptRequest
    required_patterns: dict[str, list[str]] = Field(default_factory=dict)
    unknown_sections: list[str] = Field(default_factory=list)
    forbidden_phrases: list[str] = Field(default_factory=list)

    @field_validator("required_patterns", "unknown_sections")
    @classmethod
    def known_sections(cls, value):
        if any(section not in SECTION_TITLES for section in value):
            raise ValueError("Evaluation expectations must use report section names")
        if isinstance(value, dict):
            for patterns in value.values():
                for pattern in patterns:
                    re.compile(pattern)
        return value


def body_metrics(report: dict) -> dict:
    """Exclude greetings/sign-offs and fixed unknown messages from style measurements."""
    text = "\n".join(
        report[key]["text"] for key in SECTION_TITLES if report[key]["text"] != UNKNOWN
    )
    words = re.findall(r"\b[\w’']+\b", text)
    sentences = [s for s in re.split(r"[.!?]+(?:\s|$)|\n+", text) if s.strip()]
    return {
        "words": len(words),
        "mean_words_per_sentence": round(len(words) / max(len(sentences), 1), 1),
        "contractions": len(re.findall(r"\b\w+['’](?:t|s|re|ve|ll|d|m)\b", text, re.I)),
        "exclamations": text.count("!"),
        "emoji": len(re.findall(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", text)),
    }


def assess_pair(case: EvaluationCase, drafts: list[dict]) -> dict:
    """Flag controlled fixture violations; lexical matches are not truth verification."""
    checks = []
    for draft in drafts:
        issues = []
        recipient = draft["request"]["recipient_id"]
        for section, patterns in case.required_patterns.items():
            text = draft["report"][section]["text"]
            for pattern in patterns:
                if not re.search(pattern, text, re.I):
                    issues.append(
                        {"code": "expected_fact_not_found", "section": section, "pattern": pattern}
                    )
        for section in case.unknown_sections:
            if draft["report"][section]["text"] != UNKNOWN:
                issues.append({"code": "unknown_became_a_claim", "section": section})
        for phrase in case.forbidden_phrases:
            if phrase.casefold() in draft["text"].casefold():
                issues.append({"code": "historical_or_injected_content", "phrase": phrase})
        # A novel number is a useful review flag, not a proof of hallucination:
        # legitimate arithmetic and equivalent numeric spellings also need review.
        number_pattern = r"\b\d+(?:\.\d+)?%?"
        source_numbers = set(
            re.findall(number_pattern, "\n".join(e["text"] for e in draft["evidence"]))
        )
        report_numbers = set(
            re.findall(
                number_pattern, "\n".join(draft["report"][key]["text"] for key in SECTION_TITLES)
            )
        )
        for number in sorted(report_numbers - source_numbers):
            issues.append({"code": "number_absent_from_evidence", "number": number})
        if draft["status"] != "pending" or draft["approval"] or draft["receipt"]:
            issues.append({"code": "unexpected_workflow_state"})
        checks.append(
            {
                "recipient_id": recipient,
                "flags": issues,
                "body_metrics": body_metrics(draft["report"]),
            }
        )
    bodies = [{key: d["report"][key]["text"] for key in SECTION_TITLES} for d in drafts]
    same_evidence = drafts[0]["evidence"] == drafts[1]["evidence"]
    different_bodies = bodies[0] != bodies[1]
    flags = []
    if not same_evidence:
        flags.append("different_activity_evidence")
    if not different_bodies:
        flags.append("no_body_style_variation")
    return {
        "case_id": case.case_id,
        "provenance": case.provenance,
        "same_evidence": same_evidence,
        "body_wording_differs": different_bodies,
        "pair_flags": flags,
        "recipients": checks,
        "human_review": {
            "facts_preserved_in_both": None,
            "uncertainty_preserved": None,
            "recognizable_recipient_styles": None,
            "no_claims_borrowed_from_examples": None,
        },
    }


async def evaluate(cases, seeds, settings, *, provider=None) -> dict:
    """Writes a private evidence pack. Only creates pending drafts; no delivery action."""
    if len(seeds) != 2 or len({seed.recipient_id for seed in seeds}) != 2:
        raise ValueError("Use exactly two distinct recipient profiles")
    if not cases or len({case.case_id for case in cases}) != len(cases):
        raise ValueError("Use non-empty cases with distinct IDs")
    if len({case.request.activity.session_id for case in cases}) != len(cases):
        raise ValueError(
            "Use distinct session IDs so one case cannot reuse another case's evidence"
        )
    if (settings.data_dir / "backend.sqlite3").exists():
        raise ValueError("Use a fresh evaluation directory, not existing application storage")
    # A dedicated run directory prevents changing personas/drafts used by the actual app.
    settings.live_delivery = False
    settings.heartbeat_enabled = False
    settings.prepare()
    store = Store(settings.data_dir / "backend.sqlite3")
    retrieval = RetrievalService(store)
    mode = (
        "test_double"
        if provider is not None
        else ("offline_rehearsal" if settings.provider == "demo" else "live_model")
    )
    result = {
        "mode": mode,
        "status": "running",
        "cases": [],
        "failures": [],
        "outbound_messages": 0,
        "human_acceptance": "pending",
    }
    async with httpx.AsyncClient(
        timeout=settings.request_timeout, follow_redirects=False
    ) as client:
        model = provider or make_provider(settings, client)
        result["provider"] = model.name
        personas = PersonaService(settings, store, model)
        engine = DraftEngine(settings, store, retrieval, model)
        workflow = Workflow(store, engine, DeliveryGateway(settings, client))
        for seed in seeds:
            try:
                profile = await personas.create(seed)
                # Verify the portable file is usable before handing it to Member 3.
                profile_from_soul(profile.soul_md, recipient_id=seed.recipient_id)
                write_private(
                    settings.data_dir / "profiles" / (seed.recipient_id + ".md"), profile.soul_md
                )
            except ServiceError as error:
                result["failures"].append(
                    {"stage": "persona", "recipient_id": seed.recipient_id, "code": error.code}
                )
        if not result["failures"]:
            for case in cases:
                record = redact_value(case.request.activity.model_dump(mode="json"))
                try:
                    retrieval.upsert(record)
                except ServiceError as error:
                    result["failures"].append(
                        {"stage": "activity", "case_id": case.case_id, "code": error.code}
                    )
                    continue
                drafts = []
                for seed in seeds:
                    request = DraftRequest(
                        recipient_id=seed.recipient_id,
                        retrieval=RetrievalRequest(session_ids=[record["session_id"]]),
                        destination=Destination(target="evaluation-never-send"),
                        question=case.request.question,
                    )
                    try:
                        draft = await workflow.create(request)
                        drafts.append(draft)
                        write_private(
                            settings.data_dir / case.case_id / (seed.recipient_id + ".json"),
                            json.dumps(draft, indent=2, ensure_ascii=False),
                        )
                    except ServiceError as error:
                        result["failures"].append(
                            {
                                "stage": "draft",
                                "case_id": case.case_id,
                                "recipient_id": seed.recipient_id,
                                "code": error.code,
                            }
                        )
                if len(drafts) == 2:
                    assessment = assess_pair(case, drafts)
                    result["cases"].append(assessment)
                    write_private(
                        settings.data_dir / case.case_id / "source.json",
                        json.dumps(record, indent=2),
                    )
                    write_private(
                        settings.data_dir / case.case_id / "review.md",
                        review_markdown(case, drafts, assessment),
                    )
    has_flags = any(
        c["pair_flags"] or any(r["flags"] for r in c["recipients"]) for c in result["cases"]
    )
    result["status"] = (
        "generation_failed"
        if result["failures"]
        else "needs_live_model"
        if mode != "live_model"
        else "review_flags"
        if has_flags
        else "needs_human_review"
    )
    result["automatic_checks_have_flags"] = has_flags
    result["record_hashes"] = [
        canonical_hash(redact_value(c.request.activity.model_dump(mode="json"))) for c in cases
    ]
    write_private(
        settings.data_dir / "evaluation.json", json.dumps(result, indent=2, ensure_ascii=False)
    )
    return result


def review_markdown(case, drafts, assessment):
    lines = [
        f"# Persona review: {case.case_id}",
        "",
        f"Activity provenance: {case.provenance}",
        "",
        "These automatic checks are lexical diagnostics, not semantic or style certification.",
        "Compare both drafts with source.json and the profiles/ examples before signing off.",
        "",
    ]
    for item in assessment["human_review"]:
        lines.append(f"- [ ] {item.replace('_', ' ')}")
    for draft in drafts:
        lines.extend(
            [
                "",
                "## " + draft["request"]["recipient_id"],
                "",
                f"Provider: {draft['provider']} · status: {draft['status']}",
                "",
            ]
        )
        lines.extend("> " + line for line in draft["text"].splitlines())
    lines.extend(
        [
            "",
            "## Automatic diagnostics",
            "",
            "```json",
            json.dumps(assessment, indent=2, ensure_ascii=False),
            "```",
            "",
        ]
    )
    return "\n".join(lines)
