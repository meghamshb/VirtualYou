"""Versioned interfaces for persona, retrieval, and review. No raw logs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from virtual_you.contracts.activity import ActivityRecord

# These phrases may be inserted outside the evidence-backed report by Member 3.
# Keep them non-factual: a model must not smuggle an old claim into a closing.
GREETINGS = ("", "Hello,", "Hi,", "Hey,", "Hey!", "Hi!", "Dear colleague,")
SIGN_OFFS = ("", "Thanks", "Thank you", "Regards", "Best regards", "Kind regards", "Best", "Cheers")
VerbatimText = Annotated[str, StringConstraints(strip_whitespace=False)]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PersonaSeed(Contract):
    recipient_id: str = Field(min_length=1, max_length=80, pattern=r"^[\w.-]+$")
    display_name: str = Field(min_length=1, max_length=120)
    messages: list[VerbatimText] = Field(default_factory=list, max_length=20)

    @field_validator("messages")
    @classmethod
    def meaningful_messages(cls, value):
        if any(not message.strip() or len(message) > 4000 for message in value):
            raise ValueError("Each example must contain 1–4000 characters")
        return value


class PersonaStyle(Contract):
    tone: str = Field(min_length=1, max_length=300)
    formality: Literal["casual", "neutral", "formal"]
    greeting: str = Field(max_length=120, json_schema_extra={"enum": list(GREETINGS)})
    sign_off: str = Field(max_length=120, json_schema_extra={"enum": list(SIGN_OFFS)})
    sentence_style: str = Field(min_length=1, max_length=300)
    vocabulary: list[str] = Field(max_length=20)
    punctuation: str = Field(max_length=300)
    emoji: str = Field(max_length=300)

    @field_validator("greeting")
    @classmethod
    def generic_greeting(cls, value):
        if value not in GREETINGS:
            raise ValueError("Choose a supported generic greeting without names or claims")
        return value

    @field_validator("sign_off")
    @classmethod
    def generic_sign_off(cls, value):
        if value not in SIGN_OFFS:
            raise ValueError("Choose a supported generic sign-off without names or claims")
        return value


class PersonaProfile(Contract):
    schema_version: Literal["1.0"] = "1.0"
    recipient_id: str
    display_name: str
    version: int = Field(ge=1)
    style: PersonaStyle
    examples: list[VerbatimText] = Field(default_factory=list, max_length=5)
    created_at: str
    provider: str
    soul_md: VerbatimText
    seed_message_count: Optional[int] = Field(default=None, ge=0, le=20)


class RetrievalRequest(Contract):
    sort: Literal["relevance", "recent"] = "relevance"
    query: str = Field(default="", max_length=1000)
    session_ids: list[str] = Field(default_factory=list, max_length=20)
    project_ids: Optional[list[str]] = Field(default=None, max_length=100)
    sources: Optional[list[Literal["claude", "cursor", "codex", "voice", "git"]]] = None
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    limit: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="after")
    def valid_dates(self):
        for name in ("since", "until"):
            value = getattr(self, name)
            if value is not None:
                setattr(
                    self,
                    name,
                    value.replace(tzinfo=timezone.utc)
                    if value.tzinfo is None
                    else value.astimezone(timezone.utc),
                )
        if self.since and self.until and self.since > self.until:
            raise ValueError("since must not be after until")
        return self


class Destination(Contract):
    platform: Literal["slack", "discord"] = "slack"
    target: str = Field(min_length=1, max_length=120)
    send_as: Literal["bot", "user"] = "bot"
    thread_ts: Optional[str] = Field(default=None, pattern=r"^\d+\.\d+$")


class DraftRequest(Contract):
    recipient_id: str = Field(min_length=1, max_length=80)
    retrieval: RetrievalRequest = Field(default_factory=RetrievalRequest)
    destination: Destination
    question: Optional[str] = Field(default=None, min_length=1, max_length=1000)


class Evidence(Contract):
    evidence_id: str
    session_id: str
    source: str
    field: str
    text: str
    ended_at: str
    record_hash: str


class AssembledPrompt(Contract):
    system: str
    user: str
    evidence: list[Evidence]
    persona_version: int


class PromptRequest(Contract):
    """Member 2 handoff: one normalized record, with no delivery side effects."""

    recipient_id: str = Field(min_length=1, max_length=80, pattern=r"^[\w.-]+$")
    activity: ActivityRecord
    question: Optional[str] = Field(default=None, min_length=1, max_length=1000)

    @field_validator("activity", mode="before")
    @classmethod
    def explicitly_redacted(cls, value):
        if isinstance(value, ActivityRecord):
            explicit = "redacted" in value.model_fields_set and value.redacted is True
        else:
            explicit = isinstance(value, dict) and value.get("redacted") is True
        if not explicit:
            raise ValueError("Activity must explicitly declare redacted: true")
        return value


class Citation(Contract):
    evidence_id: str
    quote: str = Field(min_length=1, max_length=1500)


class ReportSection(Contract):
    text: str = Field(min_length=1, max_length=1500)
    citations: list[Citation] = Field(default_factory=list, max_length=10)


SECTION_TITLES = {
    "starting_state": "Starting state",
    "approach": "Approach and rationale",
    "changes": "Mid-task changes",
    "result": "Final result",
    "links": "Links",
    "blockers": "Blockers",
}


class DraftReport(Contract):
    starting_state: ReportSection
    approach: ReportSection
    changes: ReportSection
    result: ReportSection
    links: ReportSection
    blockers: ReportSection


class RevisionRequest(Contract):
    expected_revision: int = Field(ge=1)


class EditRequest(RevisionRequest):
    text: str = Field(min_length=1, max_length=12000)
    destination: Optional[Destination] = None


class ApprovalDecision(RevisionRequest):
    action: Literal["approve", "reject"]


class DeliveryReceipt(Contract):
    draft_id: str
    revision: int
    status: Literal["delivered", "simulated", "failed", "unknown"]
    platform: str
    target: str
    message_id: Optional[str] = None
    error_code: Optional[str] = None
    attempted_at: str


class ReconcileDelivery(RevisionRequest):
    outcome: Literal["confirmed_delivered", "confirmed_not_delivered"]
    message_id: Optional[str] = Field(default=None, max_length=120)
    note: str = Field(min_length=10, max_length=1000)

    @model_validator(mode="after")
    def needs_receipt(self):
        if self.outcome == "confirmed_delivered" and not self.message_id:
            raise ValueError("A verified provider message ID is required")
        return self


class SourceReference(BaseModel):
    # Old providers may still return quote; only the source ID is authoritative.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    evidence_id: str = Field(min_length=1)


class ReplyParagraph(Contract):
    text: str = Field(min_length=1, max_length=1500)
    citations: list[SourceReference] = Field(default_factory=list, max_length=10)


class ConversationalReply(Contract):
    # Server resolves source references to exact excerpts, not model-written quotes.
    paragraphs: list[ReplyParagraph] = Field(min_length=1, max_length=5)
    search_query: str = Field(default="", max_length=300)
