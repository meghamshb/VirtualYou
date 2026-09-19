"""Short-lived, redacted conversation context for DM follow-ups.

Conversation history resolves references and records what was successfully
communicated. It is never work evidence: activity evidence remains the only
factual source for a reply. A delivered turn stores only stable references to
the cited activity evidence, so later replies can avoid repeating it.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from contextlib import nullcontext
from dataclasses import dataclass

from virtual_you.ingest.redact import redact_text


RETENTION_SECONDS = 3 * 24 * 60 * 60
_TOPIC_RE = re.compile(
    r"\b((?:[A-Za-z0-9][A-Za-z0-9_.-]*\s+){0,3}"
    r"(?:connector|integration|callback|auth(?:entication)?(?:\s+flow)?))\b",
    re.IGNORECASE,
)
_LEADING_NOISE = re.compile(
    r"^(?:a|an|the|and|any|on|about|for|to|of|in|with|our|your|what|have|has|did|"
    r"you|progress|changed|change|updated|update|new)\s+",
    re.IGNORECASE,
)
_REFERENCE_RE = re.compile(
    r"\b(?:it|that|this|the\s+(?:connector|integration|callback)|anything)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Resolution:
    query: str
    topics: tuple[str, ...] = ()
    ambiguous_topics: tuple[str, ...] = ()

    @property
    def is_ambiguous(self) -> bool:
        return bool(self.ambiguous_topics)


@dataclass(frozen=True)
class ConversationMemory:
    """Bounded prompt context plus the evidence already delivered in a DM."""

    history: tuple[dict[str, str], ...]
    delivered_evidence_refs: tuple[dict[str, str], ...]
    has_prior_delivery: bool


def conversation_id(channel: str, thread_ts: str | None = None) -> str:
    """Keep context isolated to one DM or one thread in a channel."""

    return "{}:{}".format(channel, thread_ts or channel)


class ConversationContext:
    def __init__(self, store) -> None:
        self.store = store
        with store.connection(write=True) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    conversation_id TEXT NOT NULL,
                    message_ts TEXT NOT NULL,
                    participant_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    evidence_refs TEXT NOT NULL DEFAULT '[]',
                    delivered_at REAL,
                    PRIMARY KEY (conversation_id, message_ts)
                );
                CREATE INDEX IF NOT EXISTS conversation_turns_expiry
                    ON conversation_turns(expires_at);
                CREATE TABLE IF NOT EXISTS context_nodes (
                    conversation_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    aliases TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    last_seen_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY (conversation_id, node_id)
                );
                CREATE INDEX IF NOT EXISTS context_nodes_active
                    ON context_nodes(conversation_id, expires_at, last_seen_at);
                CREATE TABLE IF NOT EXISTS context_edges (
                    conversation_id TEXT NOT NULL,
                    from_node_id TEXT NOT NULL,
                    to_node_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_message_ts TEXT NOT NULL,
                    last_seen_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY (conversation_id, from_node_id, to_node_id, relation)
                );
                CREATE INDEX IF NOT EXISTS context_edges_expiry
                    ON context_edges(expires_at);
                """
            )
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(conversation_turns)")
            }
            for name, definition in (
                ("evidence_refs", "TEXT NOT NULL DEFAULT '[]'"),
                ("delivered_at", "REAL"),
            ):
                if name not in columns:
                    db.execute(
                        "ALTER TABLE conversation_turns ADD COLUMN {} {}".format(
                            name, definition
                        )
                    )
            db.execute(
                """CREATE INDEX IF NOT EXISTS conversation_turns_deliveries
                ON conversation_turns(conversation_id, role, delivered_at DESC)"""
            )
        self.purge_expired()

    def purge_expired(self, *, now: float | None = None) -> None:
        """Remove all expired turn and graph data, including while the worker runs."""

        timestamp = time.time() if now is None else now
        with self.store.connection(write=True) as db:
            self._purge(db, timestamp)

    def record_turn(
        self,
        *,
        conversation: str,
        message_ts: str,
        participant_id: str,
        role: str,
        text: str,
        created_at: float | None = None,
        delivered_at: float | None = None,
        evidence_refs: list[dict] | tuple[dict, ...] | None = None,
        db=None,
    ) -> None:
        now = time.time() if created_at is None else created_at
        safe_text = redact_text(text).strip()[:4000]
        if not safe_text:
            return
        references = _normalise_evidence_refs(evidence_refs or ())
        topics = _topics(safe_text)
        with (nullcontext(db) if db is not None else self.store.connection(write=True)) as db:
            self._purge(db, now)
            db.execute(
                """INSERT OR IGNORE INTO conversation_turns(
                conversation_id,message_ts,participant_id,role,text,created_at,
                expires_at,evidence_refs,delivered_at
                ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    conversation,
                    message_ts,
                    participant_id,
                    role,
                    safe_text,
                    now,
                    now + RETENTION_SECONDS,
                    json.dumps(references),
                    delivered_at,
                ),
            )
            node_ids = []
            for label in topics:
                node_id = _node_id(label)
                node_ids.append(node_id)
                aliases = json.dumps(_aliases(label))
                db.execute(
                    """INSERT INTO context_nodes VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(conversation_id,node_id) DO UPDATE SET
                      label=excluded.label, aliases=excluded.aliases,
                      confidence=excluded.confidence,
                      last_seen_at=excluded.last_seen_at,
                      expires_at=excluded.expires_at""",
                    (
                        conversation,
                        node_id,
                        label,
                        aliases,
                        1.0,
                        now,
                        now + RETENTION_SECONDS,
                    ),
                )
            for from_id, to_id in zip(node_ids, node_ids[1:]):
                db.execute(
                    """INSERT INTO context_edges VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(conversation_id,from_node_id,to_node_id,relation)
                    DO UPDATE SET confidence=excluded.confidence,
                      source_message_ts=excluded.source_message_ts,
                      last_seen_at=excluded.last_seen_at,
                      expires_at=excluded.expires_at""",
                    (
                        conversation,
                        from_id,
                        to_id,
                        "related_to",
                        1.0,
                        message_ts,
                        now,
                        now + RETENTION_SECONDS,
                    ),
                )

    def resolve(self, conversation: str, message: str, *, now: float | None = None) -> Resolution:
        """Resolve recent references without asserting facts about work."""

        current = redact_text(message).strip()[:4000]
        timestamp = time.time() if now is None else now
        with self.store.connection(write=True) as db:
            self._purge(db, timestamp)
            rows = db.execute(
                """SELECT label,aliases,last_seen_at FROM context_nodes
                WHERE conversation_id=? AND expires_at>? ORDER BY last_seen_at DESC""",
                (conversation, timestamp),
            ).fetchall()
        nodes = [
            (row["label"], tuple(json.loads(row["aliases"])), row["last_seen_at"])
            for row in rows
        ]
        direct = _topics(current)
        generic_direct = [topic for topic in direct if _is_generic_topic(topic)]
        if generic_direct and len(generic_direct) == len(direct):
            candidates = _reference_candidates(nodes, current)
            if len(candidates) == 1:
                return Resolution(_query(current, candidates), tuple(candidates))
            if len(candidates) > 1:
                return Resolution(current, ambiguous_topics=tuple(candidates[:2]))
        if direct:
            return Resolution(_query(current, direct), tuple(direct))
        if not _REFERENCE_RE.search(current):
            return Resolution(current)

        candidates = _reference_candidates(nodes, current)
        if len(candidates) == 1:
            return Resolution(_query(current, candidates), tuple(candidates))
        if len(candidates) > 1:
            return Resolution(current, ambiguous_topics=tuple(candidates[:2]))
        return Resolution(current)

    def memory(
        self,
        conversation: str,
        *,
        exclude_message_ts: str | None = None,
        now: float | None = None,
        max_turns: int = 8,
    ) -> ConversationMemory:
        """Return a small prompt window and evidence cited in sent replies.

        Evidence references are identifiers and record hashes, never copied
        activity text. Conversation storage therefore supports comparison
        without becoming a second work-evidence database.
        """

        timestamp = time.time() if now is None else now
        with self.store.connection(write=True) as db:
            self._purge(db, timestamp)
            where = "conversation_id=? AND expires_at>?"
            params: list[object] = [conversation, timestamp]
            if exclude_message_ts is not None:
                where += " AND message_ts<>?"
                params.append(exclude_message_ts)
            rows = db.execute(
                """SELECT role,text FROM conversation_turns WHERE """
                + where
                + " ORDER BY created_at DESC LIMIT ?",
                [*params, max_turns],
            ).fetchall()
            delivered = db.execute(
                """SELECT evidence_refs FROM conversation_turns
                WHERE conversation_id=? AND expires_at>? AND role='owner'
                  AND delivered_at IS NOT NULL
                ORDER BY delivered_at DESC""",
                (conversation, timestamp),
            ).fetchall()
        history = tuple(
            {"role": row["role"], "text": row["text"][:750]} for row in reversed(rows)
        )
        references: list[dict[str, str]] = []
        seen = set()
        for row in delivered:
            for reference in _load_evidence_refs(row["evidence_refs"]):
                key = (
                    reference["evidence_id"],
                    reference["session_id"],
                    reference["field"],
                    reference["record_hash"],
                )
                if key not in seen:
                    seen.add(key)
                    references.append(reference)
                if len(references) >= 100:
                    break
            if len(references) >= 100:
                break
        return ConversationMemory(
            history=history,
            delivered_evidence_refs=tuple(references),
            has_prior_delivery=bool(delivered),
        )

    @staticmethod
    def _purge(db, now: float) -> None:
        for table in ("conversation_turns", "context_nodes", "context_edges"):
            db.execute("DELETE FROM {} WHERE expires_at<=?".format(table), (now,))


def _topics(text: str) -> list[str]:
    result = []
    for match in _TOPIC_RE.finditer(text):
        label = " ".join(match.group(1).split()).strip()
        while True:
            cleaned = _LEADING_NOISE.sub("", label).strip()
            if cleaned == label:
                break
            label = cleaned
        if label and label.lower() not in {item.lower() for item in result}:
            result.append(label)
    return result


def _aliases(label: str) -> list[str]:
    lower = label.lower()
    aliases = [lower]
    for suffix in ("connector", "integration", "callback", "authentication flow", "auth flow"):
        if lower.endswith(suffix) and suffix not in aliases:
            aliases.append(suffix)
    return aliases


def _is_generic_topic(label: str) -> bool:
    return label.lower() in {
        "connector",
        "integration",
        "callback",
        "authentication flow",
        "auth flow",
    }


def _node_id(label: str) -> str:
    return hashlib.sha256(label.lower().encode()).hexdigest()[:24]


def _reference_candidates(nodes, message: str) -> list[str]:
    lower = message.lower()
    explicit = [
        label for label, aliases, _ in nodes if any(alias in lower for alias in aliases if " " in alias)
    ]
    if explicit:
        return list(dict.fromkeys(explicit))
    generic = [label for label, aliases, _ in nodes if any(alias in lower for alias in aliases)]
    if generic:
        return list(dict.fromkeys(generic))
    # A bare pronoun only inherits context when the conversation has one active topic.
    labels = list(dict.fromkeys(label for label, _, _ in nodes))
    return labels if len(labels) <= 2 else []


def _query(message: str, topics: list[str]) -> str:
    missing = [topic for topic in topics if topic.lower() not in message.lower()]
    return "{} {}".format(message, " ".join(missing)).strip()


def evidence_references(result: dict) -> list[dict[str, str]]:
    """Extract only sources that actually supported a delivered reply."""

    evidence = {
        str(item.get("evidence_id")): item
        for item in result.get("evidence", [])
        if isinstance(item, dict) and item.get("evidence_id")
    }
    cited_ids = {
        str(citation.get("evidence_id"))
        for paragraph in result.get("paragraphs", [])
        if isinstance(paragraph, dict)
        for citation in paragraph.get("citations", [])
        if isinstance(citation, dict) and citation.get("evidence_id")
    }
    return _normalise_evidence_refs(
        {
            **evidence[item_id],
            "text_hash": hashlib.sha256(
                str(evidence[item_id].get("text", "")).encode()
            ).hexdigest(),
        }
        for item_id in cited_ids
        if item_id in evidence
    )


def _normalise_evidence_refs(references) -> list[dict[str, str]]:
    fields = ("evidence_id", "session_id", "field", "record_hash", "ended_at")
    normalised = []
    seen = set()
    for reference in references:
        if not isinstance(reference, dict) or not all(reference.get(field) for field in fields):
            continue
        item = {field: str(reference[field])[:500] for field in fields}
        if reference.get("text_hash"):
            item["text_hash"] = str(reference["text_hash"])[:128]
        key = tuple(item[field] for field in fields[:4])
        if key not in seen:
            seen.add(key)
            normalised.append(item)
    return normalised[:100]


def _load_evidence_refs(value: str) -> list[dict[str, str]]:
    try:
        return _normalise_evidence_refs(json.loads(value))
    except (TypeError, ValueError):
        return []
