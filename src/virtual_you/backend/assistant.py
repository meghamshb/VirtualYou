"""Conservative work-question routing over the shared drafting and approval engine."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

from virtual_you.backend.errors import ServiceError
from virtual_you.backend.providers import UNKNOWN
from virtual_you.backend.retrieval import canonical_hash
from virtual_you.contracts.assistant import QuestionRequest
from virtual_you.contracts.reporting import ApprovalDecision, DraftRequest, utcnow
from virtual_you.ingest.redact import assert_safe_serialized, contains_secret, redact_text

UNKNOWN_ANSWER = (
    "I don't have enough information to answer this safely. The owner needs to review it."
)

# A small, explicit English intent vocabulary: compound/unrecognized requests escalate.
SUPPORTED = {
    "completed": r"(?:what (?:was|has been|is) (?:completed|finished|done)|what (?:did|have) you (?:complete|finish|do|completed|finished|done))(?: today| so far)?",
    "changes": r"(?:what (?:has )?changed|what (?:did|have) you (?:change|changed)|what changes (?:did|have) you (?:make|made))(?: today| so far)?",
    "blockers": r"(?:any blockers|are there (?:any )?blockers|what (?:are|are the) blockers|what(?:'s| is) blocking (?:you|progress)|are you blocked)",
    "status": r"(?:what(?:'s| is) (?:the |your )?(?:current )?(?:status|progress)|(?:give|send) me (?:a |an )?(?:progress|status) update)(?: today)?",
}
UNSUPPORTED = {
    "instruction_attempt": r"\b(ignore|disregard|system prompt|override|bypass|send immediately)\b",
    "deadline": r"\b(deadline|by (?:tomorrow|friday|monday)|when will|when can|eta)\b",
    "commitment": r"\b(promise|commit|guarantee|will you|can you|could you|approve|ship|deploy)\b",
    "scope_decision": r"\b(should we|shall we|prioriti[sz]e|scope|choose|decide|add a feature)\b",
    "personal_opinion": r"\b(opinion|think of|feel about|do you like|prefer|best)\b",
}


def classify_question(text):
    normalized = " ".join(text.lower().replace("’", "'").split()).strip(" ?.!，。")
    for reason, pattern in UNSUPPORTED.items():
        if re.search(pattern, normalized):
            return None, reason
    for intent, pattern in SUPPORTED.items():
        if re.fullmatch(pattern, normalized):
            return intent, None
    return None, "unsupported_question"


class AssistantService:
    def __init__(self, settings, store, retrieval, workflow):
        self.settings, self.store = settings, store
        self.retrieval, self.workflow = retrieval, workflow
        self.lock = asyncio.Lock()
        with store.connection(write=True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS assistant_requests (
                id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, draft_id TEXT UNIQUE NOT NULL,
                payload TEXT NOT NULL, created_at TEXT NOT NULL
            )""")

    def get(self, request_id):
        with self.store.connection() as db:
            row = db.execute(
                "SELECT payload FROM assistant_requests WHERE id=?", (request_id,)
            ).fetchone()
        if not row:
            raise ServiceError("question_not_found", "Question not found.", 404)
        return json.loads(row[0])

    def list_requests(self):
        with self.store.connection() as db:
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM assistant_requests ORDER BY created_at DESC LIMIT 100"
                )
            ]

    def save(self, value):
        assert_safe_serialized(value)
        with self.store.connection(write=True) as db:
            db.execute(
                "UPDATE assistant_requests SET payload=? WHERE id=?",
                (json.dumps(value), value["id"]),
            )
        return value

    def escalate(self, value, reason):
        value.update(status="escalated", reason=reason, answer=UNKNOWN_ANSWER, draft_id=None)
        return self.save(value)

    def resolve(self, request_id, note):
        value = self.get(request_id)
        if value["status"] != "escalated":
            raise ServiceError("invalid_state", "Only an open escalation can be resolved.", 409)
        value.update(status="resolved", resolution=redact_text(note), resolved_at=utcnow())
        return self.save(value)

    def evidence_problem(self, records, intent):
        if not records:
            return "missing_evidence"
        now = datetime.now(timezone.utc)
        for row in records:
            stamp = datetime.fromisoformat(row["record"]["timestamp_range"]["ended_at"])
            stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
            if stamp > now + timedelta(minutes=5):
                return "future_evidence"
            if now - stamp > timedelta(hours=self.settings.stale_hours):
                return "stale_evidence"
        if intent == "changes" and not any(
            r["record"].get("files_changed") or r["record"].get("diffs") for r in records
        ):
            return "missing_change_evidence"
        if intent in {"completed", "status"} and not any(
            r["record"].get("end_state", "").strip() for r in records
        ):
            return "missing_result_evidence"
        if intent == "blockers" and not any(
            re.search(
                r"\b(block(?:er|ed|ing|ers)?|waiting|awaiting)\b",
                r["record"].get("end_state", ""),
                re.I,
            )
            for r in records
        ):
            return "missing_blocker_evidence"
        return None

    async def ask(self, request: QuestionRequest, *, blocked_reason=None):
        if contains_secret(request.request_id):
            raise ServiceError(
                "invalid_request_id", "Use a UUID request ID without credentials.", 422
            )
        # Redact content, not identity fields; arbitrary imported text cannot select a recipient.
        request = request.model_copy(deep=True)
        request.question = redact_text(request.question)
        request.retrieval.query = redact_text(request.retrieval.query)
        fingerprint = canonical_hash(request.model_dump(mode="json"))
        async with self.lock:
            with self.store.connection(write=True) as db:
                row = db.execute(
                    "SELECT fingerprint,payload FROM assistant_requests WHERE id=?",
                    (request.request_id,),
                ).fetchone()
                if row:
                    if row[0] != fingerprint:
                        raise ServiceError(
                            "request_conflict",
                            "This request ID already belongs to different content or scope.",
                            409,
                        )
                    value = json.loads(row[1])
                    if value["status"] != "processing":
                        return value
                else:
                    value = {
                        "id": request.request_id,
                        "recipient_id": request.recipient_id,
                        "question": request.question,
                        "status": "processing",
                        "created_at": utcnow(),
                        "draft_id": str(
                            uuid5(NAMESPACE_URL, "virtual-you-question:" + request.request_id)
                        ),
                        "intent": None,
                        "reason": None,
                        "answer": None,
                    }
                    db.execute(
                        "INSERT INTO assistant_requests VALUES(?,?,?,?,?)",
                        (
                            request.request_id,
                            fingerprint,
                            value["draft_id"],
                            json.dumps(value),
                            value["created_at"],
                        ),
                    )
            intent, reason = classify_question(request.question)
            value["intent"] = intent
            if blocked_reason:
                return self.escalate(value, blocked_reason)
            if reason:
                return self.escalate(value, reason)
            problem = self.evidence_problem(self.retrieval.search(request.retrieval), intent)
            if problem:
                return self.escalate(value, problem)
            try:
                try:
                    draft = self.store.get_draft(value["draft_id"])
                except ServiceError as error:
                    if error.code != "draft_not_found":
                        raise
                    draft = await self.workflow.create(
                        DraftRequest.model_validate(request.model_dump(exclude={"request_id"})),
                        draft_id=value["draft_id"],
                    )
                section = {
                    "completed": "result",
                    "status": "result",
                    "changes": "changes",
                    "blockers": "blockers",
                }[intent]
                if draft["report"][section]["text"] == UNKNOWN:
                    self.workflow.decide(
                        draft["id"],
                        ApprovalDecision(expected_revision=draft["revision"], action="reject"),
                    )
                    return self.escalate(value, "insufficient_answer")
            except ServiceError as error:
                return self.escalate(value, error.code)
            value.update(status="draft_ready", answer="A draft is ready for the owner's review.")
            return self.save(value)

    def guard(self, draft):
        """Run inside the shared approval gate, including HTTP and Slack callers."""
        with self.store.connection() as db:
            request = db.execute(
                "SELECT payload FROM assistant_requests WHERE draft_id=?", (draft["id"],)
            ).fetchone()
            if not request:
                return
            if json.loads(request[0])["status"] != "draft_ready":
                raise ServiceError(
                    "assistant_review_required", "This question needs owner attention.", 409
                )
            records = []
            for item in draft["evidence"]:
                row = db.execute(
                    "SELECT payload,record_hash FROM activities WHERE session_id=?",
                    (item["session_id"],),
                ).fetchone()
                if not row or row[1] != item["record_hash"]:
                    raise ServiceError(
                        "evidence_changed",
                        "The supporting activity changed. Submit a fresh question.",
                        409,
                    )
                records.append({"record": json.loads(row[0])})
        if self.evidence_problem(records, json.loads(request[0])["intent"]):
            raise ServiceError(
                "evidence_expired",
                "The supporting activity is no longer fresh. Submit a fresh question.",
                409,
            )
