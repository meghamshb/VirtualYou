from __future__ import annotations

from uuid import uuid4

from virtual_you.backend.errors import ServiceError
from virtual_you.backend.retrieval import canonical_hash
from virtual_you.contracts.reporting import DraftRequest, utcnow
from virtual_you.ingest.redact import assert_safe_serialized, redact_text


def approved_hash(draft):
    return canonical_hash(
        {"text": draft["text"], "destination": draft["destination"], "revision": draft["revision"]}
    )


class Workflow:
    def __init__(self, store, engine, gateway):
        self.store, self.engine, self.gateway = store, engine, gateway

    def validate_evidence(self, draft):
        scope = DraftRequest.model_validate(draft["request"]).retrieval
        self.engine.retrieval.validate_snapshot(draft["evidence"], scope)

    @staticmethod
    def check(draft, revision, allowed):
        if draft["revision"] != revision:
            raise ServiceError(
                "revision_conflict",
                "This draft changed. Reload and review the latest version.",
                409,
            )
        if draft["status"] not in allowed:
            raise ServiceError(
                "invalid_state",
                f"This action is unavailable while the draft is {draft['status']}.",
                409,
            )

    async def create(self, request: DraftRequest, *, draft_id=None):
        self.gateway.validate_destination(request.destination.model_dump())
        generated = await self.engine.generate(request)
        request_data = request.model_dump(mode="json")
        request_data["question"] = redact_text(request.question) if request.question else None
        request_data["retrieval"]["query"] = redact_text(request.retrieval.query)
        draft = {
            "id": draft_id or str(uuid4()),
            "revision": 1,
            "status": "pending",
            "created_at": utcnow(),
            "request": request_data,
            "destination": request.destination.model_dump(),
            "approval": None,
            "receipt": None,
            "edited": False,
            **generated,
        }
        with self.store.connection(write=True) as db:
            if db.execute("SELECT 1 FROM drafts WHERE id=?", (draft["id"],)).fetchone():
                raise ServiceError("draft_already_exists", "This draft request was already saved.", 409)
            self.store.save_draft(db, draft, "created")
        return draft

    def edit(self, draft_id, request):
        text = redact_text(request.text)
        assert_safe_serialized(text)
        with self.store.connection(write=True) as db:
            draft = self.store.load_draft(db, draft_id)
            self.check(draft, request.expected_revision, {"pending", "approved", "delivery_failed"})
            if request.destination:
                destination = request.destination.model_dump()
                self.gateway.validate_destination(destination)
                draft["destination"] = destination
                draft["request"]["destination"] = destination
            draft.update(
                revision=draft["revision"] + 1,
                status="pending",
                text=text,
                approval=None,
                receipt=None,
                edited=True,
            )
            # Structured report/evidence is the original generation, not a claim to validate manual edits.
            draft["warnings"] = [
                *draft["warnings"],
                "Human-edited text; original citations may not support the edits.",
            ]
            self.store.save_draft(db, draft, "edited")
        return draft

    async def regenerate(self, draft_id, request):
        original = self.store.get_draft(draft_id)
        self.check(original, request.expected_revision, {"pending", "approved", "delivery_failed"})
        generated = await self.engine.generate(DraftRequest.model_validate(original["request"]))
        # Generation happens outside the transaction. Recheck after awaiting the model.
        with self.store.connection(write=True) as db:
            draft = self.store.load_draft(db, draft_id)
            self.check(draft, request.expected_revision, {"pending", "approved", "delivery_failed"})
            draft.update(generated)
            draft.update(
                revision=draft["revision"] + 1,
                status="pending",
                approval=None,
                receipt=None,
                edited=False,
            )
            self.store.save_draft(db, draft, "regenerated")
        return draft

    def decide(self, draft_id, request):
        with self.store.connection(write=True) as db:
            draft = self.store.load_draft(db, draft_id)
            allowed = (
                {"pending"}
                if request.action == "approve"
                else {"pending", "approved", "delivery_failed"}
            )
            self.check(draft, request.expected_revision, allowed)
            if request.action == "approve":
                self.validate_evidence(draft)
                self.gateway.validate_destination(draft["destination"])
                draft["status"] = "approved"
                draft["approval"] = {
                    "revision": draft["revision"],
                    "content_hash": approved_hash(draft),
                    "approved_at": utcnow(),
                    "actor": "authenticated_owner",
                }
            else:
                draft.update(status="rejected", approval=None)
            self.store.save_draft(db, draft, request.action)
        return draft

    async def deliver(self, draft_id, request):
        with self.store.connection(write=True) as db:
            draft = self.store.load_draft(db, draft_id)
            self.check(
                draft,
                request.expected_revision,
                {"approved", "delivery_failed", "delivered", "simulated"},
            )
            if draft["status"] in {"delivered", "simulated"}:
                return draft  # Idempotent repeat, no second network call.
            if not draft["approval"] or draft["approval"]["content_hash"] != approved_hash(draft):
                raise ServiceError(
                    "approval_mismatch",
                    "Approval does not match the current content and destination.",
                    409,
                )
            self.validate_evidence(draft)
            self.gateway.validate_destination(draft["destination"])
            draft["status"] = "delivering"
            self.store.save_draft(db, draft, "delivery_started")
        try:
            receipt = await self.gateway.send(draft)
        except Exception:
            # Unexpected adapter failure is ambiguous too; cancellation/process death is recovered on startup.
            from virtual_you.contracts.reporting import DeliveryReceipt

            receipt = DeliveryReceipt(
                draft_id=draft_id,
                revision=draft["revision"],
                status="unknown",
                platform=draft["destination"]["platform"],
                target=draft["destination"]["target"],
                attempted_at=utcnow(),
                error_code="adapter_failure",
            )
        with self.store.connection(write=True) as db:
            current = self.store.load_draft(db, draft_id)
            self.check(current, request.expected_revision, {"delivering"})
            current["receipt"] = receipt.model_dump()
            current["status"] = {"failed": "delivery_failed", "unknown": "delivery_unknown"}.get(
                receipt.status, receipt.status
            )
            self.store.save_draft(db, current, current["status"])
        return current

    def reconcile(self, draft_id, request):
        with self.store.connection(write=True) as db:
            draft = self.store.load_draft(db, draft_id)
            self.check(draft, request.expected_revision, {"delivery_unknown"})
            if request.outcome == "confirmed_delivered":
                draft["status"] = "delivered"
                draft["receipt"].update(
                    status="delivered", message_id=request.message_id, error_code=None
                )
            else:
                draft["status"] = "approved"
                draft["receipt"] = None
            self.store.save_draft(
                db, draft, "reconciled_" + request.outcome, redact_text(request.note)
            )
        return draft
