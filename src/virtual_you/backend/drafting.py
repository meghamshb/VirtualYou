from __future__ import annotations

import re
from datetime import datetime, timezone

from pydantic import ValidationError

from virtual_you.backend.errors import ServiceError
from virtual_you.backend.prompts import assemble_prompt
from virtual_you.backend.providers import UNKNOWN
from virtual_you.contracts.reporting import (
    SECTION_TITLES,
    DraftReport,
    PersonaProfile,
)
from virtual_you.ingest.redact import assert_safe_serialized

URL_RE = re.compile(r"https?://[^\s<>\"\)\]]+")


class DraftEngine:
    """Member 4 reuses this same engine. Generation never sends a message."""

    def __init__(self, settings, store, retrieval, provider):
        self.settings, self.store, self.retrieval, self.provider = (
            settings,
            store,
            retrieval,
            provider,
        )

    async def generate(self, request):
        profile = PersonaProfile.model_validate(self.store.get_persona(request.recipient_id))
        evidence = self.retrieval.evidence(request.retrieval)
        if not evidence:
            raise ServiceError(
                "nothing_to_report",
                "No activity matches this selection. Refresh or change the selection.",
                422,
            )
        prompt = assemble_prompt(profile, evidence, request.question)
        raw = await self.provider.generate(
            task="draft",
            system=prompt.system,
            user=prompt.user,
            schema=DraftReport.model_json_schema(),
        )
        try:
            report = DraftReport.model_validate(raw)
        except ValidationError as error:
            raise ServiceError(
                "invalid_report", "Model did not return all six valid report sections.", 502
            ) from error
        self.validate_grounding(report, evidence)
        parts = []
        # Greeting/sign-off come only from the style profile; they are still reviewed.
        if profile.style.greeting:
            parts.append(profile.style.greeting)
        for key, title in SECTION_TITLES.items():
            section = getattr(report, key)
            parts.append(f"{title}\n{section.text}")
        if profile.style.sign_off:
            parts.append(profile.style.sign_off)
        text = "\n\n".join(parts)
        if len(text) > 12000:
            raise ServiceError(
                "report_too_long", "Generated report is too long. Regenerate a shorter report.", 502
            )
        assert_safe_serialized(text)
        latest = max(item.ended_at for item in evidence)
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(latest)).total_seconds() / 3600
        warnings = [
            "Citation checks verify source IDs and exact quotes, not semantic entailment. Review every factual claim."
        ]
        if age > self.settings.stale_hours:
            warnings.append(f"Selected evidence is older than {self.settings.stale_hours:g} hours.")
        if any(
            item.field == "reasoning_summary"
            and ("chain-of-thought" in item.text or "Reasoning occurred; omitted." in item.text)
            for item in evidence
        ):
            warnings.append(
                "Private reasoning was omitted upstream; do not invent an approach rationale."
            )
        if self.provider.name.startswith("demo:"):
            warnings.append("Offline demo uses extractive templates, not a language model.")
        return {
            "report": report.model_dump(),
            "text": text,
            "evidence": [item.model_dump() for item in evidence],
            "persona_version": profile.version,
            "provider": self.provider.name,
            "latest_activity_at": latest,
            "warnings": warnings,
        }

    @staticmethod
    def validate_grounding(report, evidence):
        by_id = {item.evidence_id: item for item in evidence}
        allowed_urls = set(URL_RE.findall("\n".join(item.text for item in evidence)))
        for key in SECTION_TITLES:
            section = getattr(report, key)
            if not section.citations and section.text != UNKNOWN:
                raise ServiceError(
                    "unsupported_claim", "A factual section has no evidence citations.", 502
                )
            for citation in section.citations:
                item = by_id.get(citation.evidence_id)
                if item is None or citation.quote not in item.text:
                    raise ServiceError(
                        "invalid_citation",
                        "Model cited a missing source or a quote absent from the evidence.",
                        502,
                    )
            if any(url not in allowed_urls for url in URL_RE.findall(section.text)):
                raise ServiceError(
                    "invented_link", "Model returned a link absent from the evidence.", 502
                )
        assert_safe_serialized(report)
