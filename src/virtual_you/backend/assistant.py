"""Durable uncertainty handling around the existing conversational RAG engine."""

import json
import re
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

from virtual_you.backend.errors import ServiceError
from virtual_you.backend.locks import KeyedLocks
from virtual_you.backend.providers import UNKNOWN
from virtual_you.backend.retrieval import canonical_hash
from virtual_you.contracts.reporting import DraftRequest, utcnow
from virtual_you.ingest.redact import assert_safe_serialized, contains_secret, redact_text


def judgment_reason(question):
    # No factual-question whitelist: "can you explain this commit" is a normal RAG query.
    patterns = {
        "scope_decision": r"\b(should we|shall we|do you think we should|decide whether|prioriti[sz]e)\b",
        "commitment": r"\b(promise|guarantee|commit to|(?:can|could|will) you (?:please )?(?:ship|deploy|approve|commit))\b",
        "deadline": r"\bwhen (?:will|can) (?:you|we) (?:finish|deliver|ship|deploy|complete)\b",
        "personal_opinion": r"\b(in your opinion|what do you think of|how do you feel about)\b",
    }
    return next(
        (reason for reason, pattern in patterns.items() if re.search(pattern, question, re.I)), None
    )


def needs_current_evidence(question):
    historical = re.search(
        r"\b(yesterday|last week|last month|previous|historical|in commit|this commit|on \d{4}-\d{2}-\d{2})\b",
        question,
        re.I,
    )
    return not historical and bool(
        re.search(r"\b(today|current|now|latest|recent|status|progress|blockers)\b", question, re.I)
    )


def validate_request_id(request_id):
    if not re.fullmatch(r"[\w.:-]{1,160}", request_id) or contains_secret(request_id):
        raise ServiceError("invalid_request_id", "Use a UUID request ID without credentials.", 422)


class AssistantService:
    def __init__(self, settings, store, retrieval, workflow):
        self.settings, self.store, self.retrieval, self.workflow = (
            settings,
            store,
            retrieval,
            workflow,
        )
        self.locks = KeyedLocks()
        with store.connection(write=True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS assistant_requests (
                id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, draft_id TEXT UNIQUE NOT NULL,
                payload TEXT NOT NULL, created_at TEXT NOT NULL)""")

    def get(self, request_id):
        with self.store.connection() as db:
            row = db.execute(
                "SELECT payload FROM assistant_requests WHERE id=?", (request_id,)
            ).fetchone()
        if not row:
            raise ServiceError("question_not_found", "Question not found.", 404)
        return json.loads(row[0])

    def save(self, value):
        assert_safe_serialized(value)
        with self.store.connection(write=True) as db:
            db.execute(
                "UPDATE assistant_requests SET payload=? WHERE id=?",
                (json.dumps(value), value["id"]),
            )
        return value

    def list_requests(self):
        with self.store.connection() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM assistant_requests ORDER BY created_at DESC LIMIT 100"
                )
            ]

    def resolve(self, request_id, note):
        value = self.get(request_id)
        if value["status"] != "escalated":
            raise ServiceError("invalid_state", "Only an open escalation can be resolved.", 409)
        value.update(status="resolved", resolution=redact_text(note), resolved_at=utcnow())
        return self.save(value)

    def escalate(self, value, reason):
        value.update(
            status="escalated",
            reason=reason,
            reply=None,
            answer="This needs your review; no reply has been sent.",
        )
        return self.save(value)

    def evidence_problem(self, question, result):
        cited = {c["evidence_id"] for p in result.get("paragraphs", []) for c in p["citations"]}
        used = [e for e in result.get("evidence", []) if e["evidence_id"] in cited]
        if not used or any(p["text"] == UNKNOWN for p in result.get("paragraphs", [])):
            return "missing_evidence"
        now = datetime.now(timezone.utc)
        stamps = [datetime.fromisoformat(e["ended_at"]).astimezone(timezone.utc) for e in used]
        if any(stamp > now + timedelta(minutes=5) for stamp in stamps):
            return "future_evidence"
        if needs_current_evidence(question) and max(stamps) < now - timedelta(
            hours=self.settings.stale_hours
        ):
            return "stale_evidence"
        return None

    async def prepare(
        self,
        request_id,
        *,
        question,
        recipient_id,
        scope,
        style,
        context=None,
        blocked_reason=None,
        thread_context=None,
    ):
        validate_request_id(request_id)
        question = redact_text(question)[:4000]
        # DM lookback timestamps move on replay. API callers include their explicit
        # retrieval filters in context; all callers bind audience and current style.
        fingerprint = canonical_hash(
            {
                "question": question,
                "style": style,
                "recipient": recipient_id,
                "projects": scope.project_ids,
                "sources": scope.sources,
                "context": context or {},
            }
        )
        async with self.locks.hold(request_id):
            with self.store.connection(write=True) as db:
                row = db.execute(
                    "SELECT fingerprint,payload FROM assistant_requests WHERE id=?", (request_id,)
                ).fetchone()
                if row:
                    if row[0] != fingerprint:
                        raise ServiceError(
                            "request_conflict",
                            "This request ID belongs to another question or audience.",
                            409,
                        )
                    value = json.loads(row[1])
                    if value["status"] != "processing" and not blocked_reason:
                        return value
                else:
                    value = {
                        "id": request_id,
                        "recipient_id": recipient_id,
                        "question": question,
                        "context": context or {},
                        "status": "processing",
                        "reason": None,
                        "created_at": utcnow(),
                        "draft_id": str(uuid5(NAMESPACE_URL, "virtual-you-question:" + request_id)),
                    }
                    assert_safe_serialized(value)
                    db.execute(
                        "INSERT INTO assistant_requests VALUES(?,?,?,?,?)",
                        (
                            request_id,
                            fingerprint,
                            value["draft_id"],
                            json.dumps(value),
                            value["created_at"],
                        ),
                    )
            reason = blocked_reason or judgment_reason(question)
            if reason:
                return self.escalate(value, reason)
            try:
                result = await self.workflow.engine.reply(
                    question=question,
                    scope=scope,
                    style=style,
                    **({"thread_context": thread_context} if thread_context else {}),
                )
            except ServiceError as error:
                return self.escalate(value, error.code)
            reason = self.evidence_problem(question, result)
            if reason:
                return self.escalate(value, reason)
            value.update(status="draft_ready", reply=result)
            return self.save(value)

    async def ask(self, request, *, blocked_reason=None):
        validate_request_id(request.request_id)
        request = request.model_copy(deep=True)
        request.retrieval.query = redact_text(request.retrieval.query)
        if not blocked_reason:
            from virtual_you.backend.slack_policy import scope_slack_question

            request = scope_slack_question(self.store, request)
        profile = self.store.get_persona(request.recipient_id)
        result = await self.prepare(
            request.request_id,
            question=request.question,
            recipient_id=request.recipient_id,
            scope=request.retrieval,
            style=profile["style"],
            context={
                "destination": request.destination.model_dump(),
                "retrieval": request.retrieval.model_dump(mode="json"),
            },
            blocked_reason=blocked_reason,
        )
        if result["status"] == "draft_ready":
            async with self.locks.hold("draft:" + request.request_id):
                try:
                    self.store.get_draft(result["draft_id"])
                except ServiceError as error:
                    if error.code != "draft_not_found":
                        raise
                    await self.workflow.create_reply(
                        DraftRequest.model_validate(request.model_dump(exclude={"request_id"})),
                        result["reply"],
                        draft_id=result["draft_id"],
                        persona_version=profile["version"],
                    )
        return result

    def guard(self, draft):
        if draft.get("kind") != "reply":
            return
        reason = self.evidence_problem(draft["request"].get("question", ""), draft)
        if reason:
            raise ServiceError(
                "evidence_expired", "Prepare a fresh draft before approval or delivery.", 409
            )
