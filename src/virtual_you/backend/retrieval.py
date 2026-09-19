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

STOP_WORDS = set(
    "what when where why how the a an is are was were to you your can could please me give of and in on for about did do does it we i tell have has with my any this that".split()
)


def query_terms(query):
    return [word for word in re.findall(r"\w+", query.lower()) if word not in STOP_WORDS][:20]


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
    fields += [(f"prompts.{i}", text) for i, text in enumerate(record.prompts)]
    fields += [
        (f"files_changed.{i}", f"{item.operation.value}: {item.path}")
        for i, item in enumerate(record.files_changed)
    ]
    fields += [(f"diffs.{i}", text) for i, text in enumerate(record.diffs)]
    fields += [
        (
            f"tool_calls.{i}",
            f"{item.name} [{item.status}]\nRequested input: {item.input_summary}\nRecorded result: {item.result_summary}",
        )
        for i, item in enumerate(record.tool_calls)
    ]
    result = []
    for field, text in fields:
        if not text.strip():
            continue
        # Index every field, including the tail of long patches/tool results.
        # Overlap keeps a line or short quote searchable across chunk boundaries.
        for offset in range(0, len(text), 1280):
            name = field if offset == 0 else f"{field}@{offset}"
            result.append(
                Evidence(
                    evidence_id=hashlib.sha256(
                        f"{record.source}:{record.session_id}:{record_hash}:{name}".encode()
                    ).hexdigest()[:20],
                    session_id=record.session_id,
                    source=record.source,
                    field=name,
                    text=text[offset : offset + 1500],
                    ended_at=as_utc(record.timestamp_range.ended_at),
                    record_hash=record_hash,
                )
            )
    return result


class RetrievalService:
    def __init__(self, store: Store):
        self.store = store
        # Rebuild the old truncated search index once, including unchanged records.
        if self.store.metadata("retrieval_index_version") != 2:
            with self.store.connection(write=True) as db:
                db.execute("DELETE FROM activity_search")
                for row in db.execute("SELECT id,payload,record_hash FROM activities").fetchall():
                    record = ActivityRecord.model_validate(json.loads(row["payload"]))
                    text = "\n".join(e.text for e in evidence_for(record, row["record_hash"]))
                    db.execute("UPDATE activities SET search_text=? WHERE id=?", (text, row["id"]))
                    db.execute(
                        "INSERT INTO activity_search(rowid,search_text) VALUES(?,?)",
                        (row["id"], text),
                    )
            self.store.set_metadata("retrieval_index_version", 2)

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
                db.execute("UPDATE activities SET origin=? WHERE id=?", (origin, existing["id"]))
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
        tokens = query_terms(request.query)
        if request.query and not tokens:
            return []
        join, order = "", "a.ended_at DESC, a.session_id"
        if tokens:
            join = "JOIN activity_search ON activity_search.rowid=a.id"
            predicates.append("activity_search MATCH ?")
            params.append(" OR ".join('"' + token + '"' for token in tokens))
            order = ("a.ended_at DESC, bm25(activity_search)" if request.sort == "recent"
                     else "bm25(activity_search), a.ended_at DESC")
        if request.project_ids is not None:
            if not request.project_ids:
                return []
            predicates.append(
                "EXISTS (SELECT 1 FROM activity_projects p WHERE p.session_id=a.session_id AND p.project_id IN ("
                + ",".join("?" for _ in request.project_ids)
                + "))"
            )
            params.extend(request.project_ids)
        if request.sources is not None:
            if not request.sources:
                return []
            predicates.append(
                "json_extract(a.payload,'$.source') IN ("
                + ",".join("?" for _ in request.sources)
                + ")"
            )
            params.extend(request.sources)
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

    def evidence(self, request, *, budget=18000):
        candidates = [
            item
            for row in self.search(request)
            for item in evidence_for(
                ActivityRecord.model_validate(row["record"]), row["record_hash"]
            )
        ]
        terms = set(re.findall(r"\w+", request.query.lower())) - {
            "what",
            "when",
            "where",
            "why",
            "how",
            "the",
            "a",
            "is",
            "are",
            "to",
            "you",
            "your",
            "can",
            "could",
            "please",
            "me",
            "give",
            "of",
            "and",
            "in",
        }

        def score(item):
            tokens = set(re.findall(r"\w+", item.text.lower()))
            matches = len(terms & tokens)
            # For reports, favor results and recorded rationale before patches.
            priority = 1 if item.field in {"end_state", "start_state", "reasoning_summary"} else 0
            return ((item.ended_at, priority, matches) if request.sort == "recent"
                    else (matches, priority, item.ended_at))

        candidates.sort(key=score, reverse=True)
        # Preserve human-readable outcomes alongside matching code chunks so
        # code/test repetitions cannot crowd all session/commit summaries out.
        summaries = [item for item in candidates if item.field == "end_state"][:8]
        chosen, remaining = [], budget
        summary_budget = min(6000, budget // 3)
        for item in summaries:
            if len(item.text) <= summary_budget:
                chosen.append(item)
                remaining -= len(item.text)
                summary_budget -= len(item.text)
        selected_ids = {item.evidence_id for item in chosen}
        for item in candidates:
            if item.evidence_id in selected_ids:
                continue
            if len(item.text) <= remaining:
                chosen.append(item)
                remaining -= len(item.text)
            if len(chosen) >= 24:
                break
        return chosen

    def validate_snapshot(self, evidence, scope):
        """Fail closed if a reviewed draft's evidence changed or left its audience."""
        hashes = {e["session_id"]: e["record_hash"] for e in evidence}
        if not hashes:
            return
        request = scope.model_copy(
            update={
                "query": "",
                "since": None,
                "until": None,
                "session_ids": list(hashes),
                "limit": 10,
            }
        )
        current = {r["record"]["session_id"]: r["record_hash"] for r in self.search(request)}
        if any(current.get(session) != digest for session, digest in hashes.items()):
            raise ServiceError(
                "evidence_changed",
                "Work evidence or its audience changed. Reject this draft and prepare a fresh reply.",
                409,
            )

    def stats(self):
        with self.store.connection() as db:
            return dict(
                db.execute(
                    "SELECT count(*) AS count,max(ended_at) AS latest_activity_at,max(indexed_at) AS last_indexed_at FROM activities"
                ).fetchone()
            )

    def assign_project(self, session_ids, project_id):
        if (
            not session_ids
            or len(session_ids) > 100
            or not project_id.strip()
            or len(project_id) > 80
        ):
            raise ServiceError("invalid_project", "Choose activities and a project name.")
        project_id = redact_value(project_id.strip())
        with self.store.connection(write=True) as db:
            for session_id in session_ids:
                if not db.execute(
                    "SELECT 1 FROM activities WHERE session_id=?", (session_id,)
                ).fetchone():
                    raise ServiceError(
                        "activity_not_found", "An activity is no longer available.", 404
                    )
                db.execute(
                    "INSERT OR REPLACE INTO activity_projects VALUES (?,?)",
                    (session_id, project_id),
                )

    def project_choices(self):
        with self.store.connection() as db:
            return [
                r[0]
                for r in db.execute(
                    "SELECT DISTINCT p.project_id FROM activity_projects p JOIN activities a ON a.session_id=p.session_id ORDER BY p.project_id"
                )
            ]

    def activity_choices(self):
        with self.store.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT a.session_id,a.payload,p.project_id FROM activities a LEFT JOIN activity_projects p ON p.session_id=a.session_id ORDER BY a.ended_at DESC LIMIT 100"
                )
            ]
