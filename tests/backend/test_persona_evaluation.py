import asyncio
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from virtual_you.backend.errors import ServiceError
from virtual_you.backend.persona_eval import EvaluationCase, body_metrics, evaluate
from virtual_you.backend.providers import UNKNOWN, DemoProvider
from virtual_you.contracts.reporting import SECTION_TITLES, PersonaSeed
from virtual_you.ingest.service import IngestionService

ROOT = Path(__file__).parents[2]


@pytest.fixture
def cases():
    return [
        EvaluationCase.model_validate(item)
        for item in json.loads((ROOT / "examples/persona/evaluation-cases.json").read_text())
    ]


@pytest.fixture
def seeds():
    return [
        PersonaSeed(
            recipient_id=recipient,
            display_name=recipient.title(),
            messages=json.loads((ROOT / f"examples/persona/{recipient}.messages.json").read_text()),
        )
        for recipient in ("manager", "teammate")
    ]


def test_offline_rehearsal_is_never_labeled_live_acceptance(cases, seeds, settings):
    result = asyncio.run(evaluate(cases, seeds, settings))
    assert result["status"] == "needs_live_model"
    assert result["mode"] == "offline_rehearsal"
    assert result["human_acceptance"] == "pending"
    assert len(result["cases"]) == 3
    for case in result["cases"]:
        assert case["same_evidence"]
        assert case["pair_flags"] == ["no_body_style_variation"]
        assert all(not recipient["flags"] for recipient in case["recipients"])
        assert all(value is None for value in case["human_review"].values())
    assert result["outbound_messages"] == 0
    for path in settings.data_dir.glob("*/*.json"):
        assert path.stat().st_mode & 0o777 == 0o600
    draft = json.loads((settings.data_dir / "completed-work/manager.json").read_text())
    assert draft["status"] == "pending"
    assert draft["approval"] is None and draft["receipt"] is None
    assert (settings.data_dir / "failed-tests/review.md").exists()


def test_body_variation_is_measured_but_not_claimed_as_persona_quality(cases, seeds, settings):
    class Varied(DemoProvider):
        async def generate(self, **kwargs):
            result = await super().generate(**kwargs)
            if kwargs["task"] == "draft":
                style = json.loads(kwargs["user"])["style_only"]
                prefix = "An update: " if style["formality"] == "formal" else "Quick update! "
                result["starting_state"]["text"] = prefix + result["starting_state"]["text"]
            return result

    result = asyncio.run(evaluate(cases, seeds, settings, provider=Varied()))
    assert result["mode"] == "test_double"
    assert result["status"] == "needs_live_model"
    assert all(case["body_wording_differs"] for case in result["cases"])
    assert result["human_acceptance"] == "pending"


@pytest.mark.parametrize(
    "mutation,expected_flag",
    [
        ("pass", "expected_fact_not_found"),
        ("number", "number_absent_from_evidence"),
        ("old-fact", "historical_or_injected_content"),
    ],
)
def test_reviewer_flags_wrong_claims_even_when_citation_quotes_are_valid(
    cases, seeds, settings, mutation, expected_flag
):
    class Contaminated(DemoProvider):
        async def generate(self, **kwargs):
            result = await super().generate(**kwargs)
            if kwargs["task"] == "draft":
                if mutation == "pass":
                    result["result"]["text"] = "2 tests passed. Not deployed; Maya must review."
                elif mutation == "number":
                    result["result"]["text"] += " 300 checks passed."
                else:
                    result["result"]["text"] += " Project Atlas is ready for Friday."
            return result

    result = asyncio.run(evaluate([cases[1]], seeds, settings, provider=Contaminated()))
    assert not result["failures"]  # Existing citation validation alone allows these paraphrases.
    assert all(
        expected_flag in [flag["code"] for flag in recipient["flags"]]
        for recipient in result["cases"][0]["recipients"]
    )
    assert result["automatic_checks_have_flags"]


def test_missing_results_are_not_accepted_as_new_claims(cases, seeds, settings):
    class Unsupported(DemoProvider):
        async def generate(self, **kwargs):
            result = await super().generate(**kwargs)
            if kwargs["task"] == "draft":
                evidence = json.loads(kwargs["user"])["evidence"][0]
                result["result"] = {
                    "text": "All tests passed.",
                    "citations": [
                        {"evidence_id": evidence["evidence_id"], "quote": evidence["text"]}
                    ],
                }
            return result

    result = asyncio.run(evaluate([cases[2]], seeds, settings, provider=Unsupported()))
    assert any(
        flag["code"] == "unknown_became_a_claim"
        for flag in result["cases"][0]["recipients"][0]["flags"]
    )


def test_provider_failure_is_recorded_without_echoing_private_error(cases, seeds, settings):
    class Unavailable(DemoProvider):
        async def generate(self, **kwargs):
            raise ServiceError("model_unavailable", "private-provider-details", 502)

    result = asyncio.run(evaluate(cases, seeds, settings, provider=Unavailable()))
    assert result["status"] == "generation_failed"
    assert not result["cases"]
    assert "private-provider-details" not in (settings.data_dir / "evaluation.json").read_text()


def test_draft_failure_does_not_claim_completed_pair(cases, seeds, settings):
    class Unavailable(DemoProvider):
        async def generate(self, **kwargs):
            if kwargs["task"] == "draft":
                raise ServiceError("model_unavailable", "unavailable", 502)
            return await super().generate(**kwargs)

    result = asyncio.run(evaluate([cases[0]], seeds, settings, provider=Unavailable()))
    assert result["status"] == "generation_failed"
    assert result["cases"] == []
    assert len(result["failures"]) == 2


@pytest.mark.parametrize("source", ["claude", "cursor"])
def test_member1_parser_to_member2_profile_to_member3_pending_draft(
    source, seeds, settings, tmp_path
):
    record = IngestionService(data_directory=tmp_path / "ingest").ingest_file(
        source, ROOT / f"tests/fixtures/{source}_session.jsonl"
    )
    case = EvaluationCase(
        case_id="ingested-fixture", request={"recipient_id": "manager", "activity": record}
    )
    result = asyncio.run(evaluate([case], seeds, settings))
    assert result["cases"][0]["same_evidence"]
    assert not result["failures"]
    saved = json.loads((settings.data_dir / "ingested-fixture/manager.json").read_text())
    assert saved["status"] == "pending"
    assert all(evidence["source"] == source for evidence in saved["evidence"])


def test_evaluation_refuses_existing_application_database(cases, seeds, settings):
    settings.data_dir.mkdir()
    path = settings.data_dir / "backend.sqlite3"
    path.write_bytes(b"do not touch")
    with pytest.raises(ValueError, match="fresh evaluation"):
        asyncio.run(evaluate(cases, seeds, settings))
    assert path.read_bytes() == b"do not touch"


def test_case_cannot_accidentally_reuse_another_cases_session(cases, seeds, settings):
    cases[1].request.activity.session_id = cases[0].request.activity.session_id
    with pytest.raises(ValueError, match="distinct session IDs"):
        asyncio.run(evaluate(cases, seeds, settings))


def test_empty_activity_does_not_create_a_successful_pair(cases, seeds, settings):
    activity = cases[0].request.activity
    activity.prompts, activity.tool_calls, activity.files_changed, activity.diffs = [], [], [], []
    activity.end_state, activity.reasoning_summary = "", ""
    result = asyncio.run(evaluate([cases[0]], seeds, settings))
    assert result["status"] == "generation_failed"
    assert result["cases"] == []
    assert result["failures"][0]["code"] == "nothing_to_report"


def test_evaluation_does_not_modify_inputs(cases, seeds, settings):
    original_cases, original_seeds = copy.deepcopy(cases), copy.deepcopy(seeds)
    asyncio.run(evaluate(cases, seeds, settings))
    assert cases == original_cases and seeds == original_seeds


def test_unknown_sections_do_not_inflate_body_style_metrics():
    report = {key: {"text": UNKNOWN, "citations": []} for key in SECTION_TITLES}
    assert body_metrics(report)["words"] == 0


def test_cli_requires_live_configuration_or_explicit_offline_choice():
    env = {**os.environ, "VIRTUAL_YOU_LLM_PROVIDER": "demo"}
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/evaluate_member2.py")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    summary = json.loads(result.stdout)
    assert summary["status"] == "blocked" and summary["completed_pairs"] == 0
    assert summary["provider"] is None
