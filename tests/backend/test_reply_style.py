import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from virtual_you.backend.errors import ServiceError
from virtual_you.backend.reply_style import normalize_reply_style, validate_reply_style
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


def test_hash_is_shortened_before_review_without_model_retry_or_citation_changes(client):
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
                        "text": "Commit " + SHA + " restored the desktop app.",
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
    assert result["model_calls"] == 1 and len(calls) == 1
    assert "not a Git log" not in calls[0]["evidence"][0]["text"]  # Instructions stay separate.
    assert {item["session_id"] for item in result["evidence"]} == {"removed", "restored"}
    assert SHA not in result["text"]
    assert result["text"] == "Commit " + SHA[:7] + " restored the desktop app."
    citation = result["paragraphs"][0]["citations"][0]
    assert SHA in citation["quote"]  # Full provenance stays in evidence, not the prose.
    assert not client.app.state.store.list_drafts()  # Generation has not approved or delivered.


@pytest.mark.parametrize("question,expected_id", [
    ("What is the latest commit?", SHA[:7]),
    ("Can you tell me about your latest commit?", SHA[:7]),
    ("What is the full commit hash?", SHA),
])
def test_latest_commit_accepts_source_url_and_preserves_exact_provenance(client, question, expected_id):
    now = datetime.now(timezone.utc).isoformat()
    url = "https://github.com/team/work/commit/" + SHA
    source_text = "Recorded Git commit " + SHA + ": Restored the desktop app. " + url
    client.app.state.retrieval.upsert({
        "session_id": "latest", "source": "git", "redacted": True,
        "end_state": source_text,
        "timestamp_range": {"started_at": now, "ended_at": now},
    })
    calls = []

    class Model:
        async def generate(self, **kwargs):
            data = json.loads(kwargs["user"])
            calls.append(data)
            source = next(item for item in data["evidence"] if item["field"] == "end_state")
            return {"paragraphs": [{
                "text": "Commit `" + SHA + "` restored the desktop app. [View commit](" + url + ")",
                "citations": [{"evidence_id": source["evidence_id"]}],
            }]}

    engine = client.app.state.engine
    engine.provider = Model()
    result = asyncio.run(engine.reply(question=question, scope=RetrievalRequest(sources=["git"]), style={}))
    assert result["model_calls"] == len(calls) == 1
    assert result["text"] == "Commit `" + expected_id + "` restored the desktop app. [View commit](" + url + ")"
    assert result["paragraphs"][0]["citations"][0]["quote"] == source_text
    assert result["paragraphs"][0]["text"] == result["text"]
    assert not client.app.state.store.list_drafts()


def test_presentation_normalization_preserves_links_and_explicit_full_hashes():
    url = "https://github.com/team/work/commit/" + SHA
    for text in (url, "[commit](" + url + ")", "<" + url + "|commit>"):
        assert normalize_reply_style(text, "What is the latest commit?") == text
        validate_reply_style(text, "What is the latest commit?")
    assert normalize_reply_style("Commit " + SHA, "Give the exact commit ID") == "Commit " + SHA
    assert normalize_reply_style("Commit " + SHA, "What changed?") == "Commit " + SHA[:7]


def test_hash_normalization_does_not_bypass_missing_citation_or_invented_link(client):
    seed_history(client)

    class Model:
        def __init__(self, citations):
            self.citations, self.calls = citations, 0

        async def generate(self, **kwargs):
            self.calls += 1
            source = json.loads(kwargs["user"])["evidence"][0]
            return {"paragraphs": [{
                "text": "Commit " + SHA + " restored the desktop app. https://evil.invalid/" + SHA,
                "citations": [{"evidence_id": source["evidence_id"]}] if self.citations else [],
            }]}

    engine = client.app.state.engine
    for cited, expected in ((False, "unsupported_claim"), (True, "invented_link")):
        engine.provider = Model(cited)
        with pytest.raises(ServiceError) as error:
            asyncio.run(engine.reply(question="Latest commit?", scope=RetrievalRequest(sources=["git"]), style={}))
        assert error.value.code == expected
        assert engine.provider.calls == 2
    assert not client.app.state.store.list_drafts()


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
