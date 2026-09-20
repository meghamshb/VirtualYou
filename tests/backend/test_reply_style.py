import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from virtual_you.backend.errors import ServiceError
from virtual_you.backend.reply_style import validate_reply_style
from virtual_you.contracts.reporting import RetrievalRequest

SHA = "a" * 40


def seed_history(client):
    now = datetime.now(timezone.utc)
    for session, message, hours in [
        ("removed", "Removed the Electron desktop app.", 2),
        (
            "restored",
            "Restored the Electron desktop app and connected its approvals to the backend.",
            1,
        ),
    ]:
        stamp = (now - timedelta(hours=hours)).isoformat()
        client.app.state.retrieval.upsert(
            {
                "session_id": session,
                "source": "git",
                "redacted": True,
                "end_state": f"Recorded Git commit {SHA}: {message}\nA commit does not verify tests or deployment.",
                "timestamp_range": {"started_at": stamp, "ended_at": stamp},
            }
        )


def test_hash_dump_is_rewritten_before_review_with_citations_and_order_preserved(client):
    seed_history(client)
    calls = []

    class Model:
        async def generate(self, **kwargs):
            data = json.loads(kwargs["user"])
            calls.append(data)
            assert "not a Git log" in kwargs["system"]
            assert "a selected newer record does not prove branch ancestry" in kwargs["system"]
            source = next(item for item in data["evidence"] if "Restored" in item["text"])
            return {
                "paragraphs": [
                    {
                        "text": (
                            "Commit " + SHA + " restored the desktop app."
                            if len(calls) == 1
                            else "- Restored the desktop app and connected its approvals to the backend."
                        ),
                        "citations": [{"evidence_id": source["evidence_id"]}],
                    }
                ],
                "search_query": "",
            }

    engine = client.app.state.engine
    engine.provider = Model()
    result = asyncio.run(
        engine.reply(question="What changed?", scope=RetrievalRequest(sources=["git"]), style={})
    )
    assert result["model_calls"] == 2
    assert "reply_identifier_dump" in calls[1]["validation_feedback"]
    assert "not a Git log" not in calls[1]["evidence"][0]["text"]  # Instructions stay separate.
    assert calls[0]["evidence"] == calls[1]["evidence"]
    assert {item["session_id"] for item in result["evidence"]} == {"removed", "restored"}
    assert SHA not in result["text"]
    assert "connected its approvals" in result["text"]
    citation = result["paragraphs"][0]["citations"][0]
    assert SHA in citation["quote"]  # Full provenance stays in evidence, not the prose.
    assert not client.app.state.store.list_drafts()  # Generation has not approved or delivered.


def test_overlong_reply_repair_is_bounded_and_does_not_silently_trim(client):
    seed_history(client)
    calls = []

    class Model:
        async def generate(self, **kwargs):
            data = json.loads(kwargs["user"])
            calls.append(data)
            source = next(item for item in data["evidence"] if "Restored" in item["text"])
            return {
                "paragraphs": [
                    {
                        "text": "Restored the Electron desktop app. " * 40,
                        "citations": [{"evidence_id": source["evidence_id"]}],
                    }
                ],
                "search_query": "",
            }

    engine = client.app.state.engine
    engine.provider = Model()
    with pytest.raises(ServiceError) as error:
        asyncio.run(
            engine.reply(
                question="Quick status?", scope=RetrievalRequest(sources=["git"]), style={}
            )
        )
    assert error.value.code == "reply_too_long"
    assert len(calls) == 2
    assert "keeping the answer and all material uncertainty" in calls[1]["validation_feedback"]
    assert not client.app.state.store.list_drafts()


def test_hash_and_detail_requests_can_receive_requested_technical_details():
    validate_reply_style("Commit " + SHA, "What is the full commit hash?")
    validate_reply_style("Commit " + SHA, "Explain commit " + SHA)
    validate_reply_style("x" * 2200, "Give a detailed explanation of these changes.")
    validate_reply_style("Commit abc1234 restored the desktop app.", "Which commit restored it?")
    with pytest.raises(ServiceError) as error:
        validate_reply_style("x" * 1300, "Quick status?")
    assert error.value.code == "reply_too_long"
