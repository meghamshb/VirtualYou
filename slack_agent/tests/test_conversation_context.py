import hashlib
import time

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


def test_out_of_order_messages_do_not_leak_future_context_or_deliveries(tmp_path):
    context = ConversationContext(Store(tmp_path / "db.sqlite"))
    conversation = conversation_id("D123")
    # Arrival order differs from Slack order, as in a paginated history poll.
    for ts, role, text in [
        ("5.0", "owner", "The Jira connector is done."),
        ("4.0", "colleague", "And the Slack connector?"),
        ("3.0", "colleague", "What changed in the connector?"),
        ("2.0", "colleague", "Any progress on the GitHub connector?"),
        ("1.0", "colleague", "Hello"),
    ]:
        context.record_turn(
            conversation=conversation, message_ts=ts, participant_id="U1",
            role=role, text=text, created_at=100,
            delivered_at=100 if role == "owner" else None,
        )
    resolved = context.resolve(
        conversation, "What changed in the connector?", now=101, before_message_ts="3.0",
    )
    assert resolved.topics == ("GitHub connector",)
    memory = context.memory(conversation, now=101, before_message_ts="3.0")
    assert [t["text"] for t in memory.history] == ["Hello", "Any progress on the GitHub connector?"]
    assert not memory.has_prior_delivery


def test_redacted_bounded_context_survives_restart_and_isolates_threads(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    context = ConversationContext(store)
    for i in range(12):
        context.record_turn(
            conversation=conversation_id("D123", "1.0"), message_ts=str(i + 2),
            participant_id="U1", role="colleague",
            text="GitHub connector password=private123 " + "word " * 200,
        )
    restarted = ConversationContext(Store(tmp_path / "db.sqlite"))
    memory = restarted.memory(conversation_id("D123", "1.0"))
    assert len(memory.history) == 8
    assert all(len(t["text"]) <= 750 and "private123" not in t["text"] for t in memory.history)
    for other in (conversation_id("DOTHER", "1.0"), conversation_id("D123", "2.0"), conversation_id("D123")):
        assert restarted.memory(other).history == ()
        assert restarted.resolve(other, "What changed in it?").topics == ()


def test_replay_does_not_extend_retention_and_all_graph_data_expires(tmp_path):
    context = ConversationContext(Store(tmp_path / "db.sqlite"))
    start = time.time()
    turn = dict(conversation="D1:D1", message_ts="1.0", participant_id="U1", role="owner",
                text="The GitHub connector and the Jira integration.", delivered_at=start)
    context.record_turn(**turn, created_at=start)
    context.record_turn(**turn, created_at=start + 200)
    with context.store.connection() as db:
        assert db.execute('SELECT count(*) FROM context_edges').fetchone()[0] == 1
    context.purge_expired(now=start + 3 * 24 * 60 * 60)
    with context.store.connection() as db:
        for table in ('conversation_turns', 'context_nodes', 'context_edges'):
            assert db.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0
    assert not context.memory('D1:D1').has_prior_delivery


def test_three_active_topics_require_clarification_and_generic_turn_is_not_a_topic(tmp_path):
    context = ConversationContext(Store(tmp_path / "db.sqlite"))
    for i, text in enumerate((
        "The GitHub connector.", "The Jira connector.", "The Slack connector.",
        "What changed in the connector?",
    )):
        context.record_turn(conversation="D1:D1", message_ts=str(i + 1), participant_id="U1",
                            role="colleague", text=text)
    result = context.resolve("D1:D1", "Did you update it?")
    assert set(result.ambiguous_topics) == {'GitHub connector', 'Jira connector', 'Slack connector'}
