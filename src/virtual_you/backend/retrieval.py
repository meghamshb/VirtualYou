"""Lexical RAG over Member 1's sanitized contract, never editor/session logs."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import timezone

from virtual_you.backend.errors import ServiceError
from virtual_you.backend.store import Store
from virtual_you.contracts.activity import ActivityRecord
from virtual_you.contracts.reporting import Evidence, RetrievalRequest, utcnow
from virtual_you.ingest.redact import assert_safe_serialized, redact_value


def canonical_hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def as_utc(value):
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    ).isoformat()


def evidence_for(record, record_hash):
    """Stable field citations, bounded snippets, metadata preserved with every quote."""
    fields = [
        ("start_state", record.start_state),
        ("reasoning_summary", record.reasoning_summary),
        ("end_state", record.end_state),
    ]
    fields += [(f"prompts.{i}", text) for i, text in enumerate(record.prompts[:5])]
    fields += [
        (f"files_changed.{i}", f"{item.operation.value}: {item.path}")
        for i, item in enumerate(record.files_changed[:15])
    ]
    fields += [(f"diffs.{i}", text) for i, text in enumerate(record.diffs[:5])]
    fields += [
        (f"tool_calls.{i}", f"{item.name} [{item.status}]: {item.result_summary}")
        for i, item in enumerate(record.tool_calls[:15])
    ]
    result, remaining = [], 10000
    for field, text in fields:
        if not text.strip() or remaining <= 0:
            continue
        text = text[: min(1500, remaining)]
        remaining -= len(text)
        result.append(
            Evidence(
                evidence_id=hashlib.sha256(
                    f"{record.source}:{record.session_id}:{record_hash}:{field}".encode()
                ).hexdigest()[:20],
                session_id=record.session_id,
                source=record.source,
                field=field,
                text=text,
                ended_at=as_utc(record.timestamp_range.ended_at),
                record_hash=record_hash,
            )
        )
    return result


class RetrievalService:
    def __init__(self, store: Store):
        self.store = store

    def upsert(self, payload, origin="api"):
        # The ActivityRecord default is true, so check the wire payload explicitly.
        if not isinstance(payload, dict) or payload.get("redacted") is not True:
            raise ServiceError(
                "unredacted_record", "Only explicitly redacted ActivityRecords are accepted.", 422
            )
        record = ActivityRecord.model_validate(redact_value(payload))
        assert_safe_serialized(record)
        if not record.has_reportable_evidence():
            raise ServiceError(
                "nothing_to_report", "Activity contains no reportable evidence.", 422
            )
        normalized = record.model_dump(mode="json")
        digest = canonical_hash(normalized)
        text = "\n".join(item.text for item in evidence_for(record, digest))
        with self.store.connection(write=True) as db:
            existing = db.execute(
                "SELECT id,record_hash,payload,ended_at FROM activities WHERE session_id=?",
                (record.session_id,),
            ).fetchone()
            if existing and json.loads(existing["payload"])["source"] != record.source:
                raise ServiceError(
                    "session_source_conflict",
                    "Session ID already belongs to a different source.",
                    409,
                )
            if existing and existing["record_hash"] == digest:
                return False
            if existing and as_utc(record.timestamp_range.ended_at) < existing["ended_at"]:
                # A delayed feed response must not roll a session back to older evidence.
                return False
            if existing:
                row_id = existing["id"]
                db.execute("DELETE FROM activity_search WHERE rowid=?", (row_id,))
                db.execute(
                    "UPDATE activities SET record_hash=?,ended_at=?,payload=?,origin=?,search_text=?,indexed_at=? WHERE id=?",
                    (
                        digest,
                        as_utc(record.timestamp_range.ended_at),
                        json.dumps(normalized),
                        origin,
                        text,
                        utcnow(),
                        row_id,
                    ),
                )
            else:
                row_id = db.execute(
                    "INSERT INTO activities(session_id,record_hash,ended_at,payload,origin,search_text,indexed_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        record.session_id,
                        digest,
                        as_utc(record.timestamp_range.ended_at),
                        json.dumps(normalized),
                        origin,
                        text,
                        utcnow(),
                    ),
                ).lastrowid
            db.execute("INSERT INTO activity_search(rowid,search_text) VALUES(?,?)", (row_id, text))
        return True

    def remove_origin(self, origin):
        with self.store.connection(write=True) as db:
            rows = db.execute("SELECT id FROM activities WHERE origin=?", (origin,)).fetchall()
            for row in rows:
                db.execute("DELETE FROM activity_search WHERE rowid=?", (row[0],))
            db.execute("DELETE FROM activities WHERE origin=?", (origin,))
        return len(rows)

    def origins(self):
        with self.store.connection() as db:
            return [row[0] for row in db.execute("SELECT DISTINCT origin FROM activities")]

    def search(self, request: RetrievalRequest):
        predicates, params = [], []
        tokens = re.findall(r"\w+", request.query, re.UNICODE)[:20]
        if request.query and not tokens:
            return []
        join, order = "", "a.ended_at DESC, a.session_id"
        if tokens:
            join = "JOIN activity_search ON activity_search.rowid=a.id"
            predicates.append("activity_search MATCH ?")
            params.append(" OR ".join('"' + token + '"' for token in tokens))
            order = "bm25(activity_search), a.ended_at DESC"
        if request.session_ids:
            predicates.append(
                "a.session_id IN (" + ",".join("?" for _ in request.session_ids) + ")"
            )
            params.extend(request.session_ids)
        if request.since:
            predicates.append("a.ended_at>=?")
            params.append(as_utc(request.since))
        if request.until:
            predicates.append("a.ended_at<=?")
            params.append(as_utc(request.until))
        where = " WHERE " + " AND ".join(predicates) if predicates else ""
        with self.store.connection() as db:
            rows = db.execute(
                f"SELECT a.* FROM activities a {join}{where} ORDER BY {order} LIMIT ?",
                [*params, request.limit],
            ).fetchall()
        return [
            {
                "record": json.loads(row["payload"]),
                "record_hash": row["record_hash"],
                "indexed_at": row["indexed_at"],
            }
            for row in rows
        ]

    def evidence(self, request):
        return [
            item
            for row in self.search(request)
            for item in evidence_for(
                ActivityRecord.model_validate(row["record"]), row["record_hash"]
            )
        ]

    def stats(self):
        with self.store.connection() as db:
            return dict(
                db.execute(
                    "SELECT count(*) AS count,max(ended_at) AS latest_activity_at,max(indexed_at) AS last_indexed_at FROM activities"
                ).fetchone()
            )
