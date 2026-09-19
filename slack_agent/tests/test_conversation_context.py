import hashlib

from virtual_you.backend.store import Store
from virtualyou_workflow.conversation_context import (
    ConversationContext,
    conversation_id,
    evidence_references,
)


def test_resolves_a_pronoun_to_the_recent_connector_topic(tmp_path):
    context = ConversationContext(Store(tmp_path / "db.sqlite"))
    conversation = conversation_id("D123")
    context.record_turn(
        conversation=conversation,
        message_ts="1.0",
        participant_id="UFRIEND",
        role="colleague",
        text="Any progress on the GitHub connector?",
        created_at=100,
    )

    resolved = context.resolve(conversation, "Have you updated it or anything?", now=101)

    assert resolved.topics == ("GitHub connector",)
    assert "GitHub connector" in resolved.query
    assert not resolved.is_ambiguous


def test_asks_for_clarification_when_two_connectors_are_active(tmp_path):
    context = ConversationContext(Store(tmp_path / "db.sqlite"))
    conversation = conversation_id("D123")
    for timestamp, text in (("1.0", "Any progress on the GitHub connector?"), ("2.0", "And the Slack connector?")):
        context.record_turn(
            conversation=conversation,
            message_ts=timestamp,
            participant_id="UFRIEND",
            role="colleague",
            text=text,
            created_at=100,
        )

    resolved = context.resolve(conversation, "Have you updated it?", now=101)

    assert resolved.is_ambiguous
    assert set(resolved.ambiguous_topics) == {"GitHub connector", "Slack connector"}


def test_resolves_a_generic_connector_reference(tmp_path):
    context = ConversationContext(Store(tmp_path / "db.sqlite"))
    conversation = conversation_id("D123")
    context.record_turn(
        conversation=conversation,
        message_ts="1.0",
        participant_id="UFRIEND",
        role="colleague",
        text="Any progress on the GitHub connector?",
        created_at=100,
    )

    resolved = context.resolve(conversation, "What changed in the connector?", now=101)

    assert resolved.topics == ("GitHub connector",)
    assert "GitHub connector" in resolved.query


def test_context_expires_after_three_days(tmp_path):
    context = ConversationContext(Store(tmp_path / "db.sqlite"))
    conversation = conversation_id("D123")
    context.record_turn(
        conversation=conversation,
        message_ts="1.0",
        participant_id="UFRIEND",
        role="colleague",
        text="Any progress on the GitHub connector?",
        created_at=100,
    )

    resolved = context.resolve(conversation, "Have you updated it?", now=100 + 3 * 24 * 60 * 60 + 1)

    assert resolved.topics == ()
    with context.store.connection() as db:
        assert db.execute("SELECT count(*) FROM conversation_turns").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM context_nodes").fetchone()[0] == 0


def test_memory_keeps_only_cited_evidence_from_successful_deliveries(tmp_path):
    context = ConversationContext(Store(tmp_path / "db.sqlite"))
    conversation = conversation_id("D123")
    result = {
        "evidence": [
            {
                "evidence_id": "shown",
                "session_id": "session-1",
                "field": "end_state",
                "record_hash": "version-1",
                "ended_at": "2026-09-19T00:00:00+00:00",
                "text": "This text must not be copied into the delivery snapshot.",
            },
            {
                "evidence_id": "not-shown",
                "session_id": "session-1",
                "field": "diffs.0",
                "record_hash": "version-1",
                "ended_at": "2026-09-19T00:00:00+00:00",
                "text": "Uncited source.",
            },
        ],
        "paragraphs": [{"text": "The connector is ready.", "citations": [{"evidence_id": "shown"}]}],
    }
    context.record_turn(
        conversation=conversation,
        message_ts="2.0",
        participant_id="UOWNER",
        role="owner",
        text="The connector is ready.",
        created_at=100,
        delivered_at=100,
        evidence_refs=evidence_references(result),
    )

    memory = context.memory(conversation, now=101)

    assert memory.has_prior_delivery
    assert memory.history == ({"role": "owner", "text": "The connector is ready."},)
    assert memory.delivered_evidence_refs == (
        {
            "evidence_id": "shown",
            "session_id": "session-1",
            "field": "end_state",
            "record_hash": "version-1",
            "ended_at": "2026-09-19T00:00:00+00:00",
            "text_hash": hashlib.sha256(
                b"This text must not be copied into the delivery snapshot."
            ).hexdigest(),
        },
    )
    with context.store.connection() as db:
        row = db.execute(
            "SELECT evidence_refs,delivered_at FROM conversation_turns"
        ).fetchone()
    assert "This text" not in row["evidence_refs"]
    assert row["delivered_at"] == 100
