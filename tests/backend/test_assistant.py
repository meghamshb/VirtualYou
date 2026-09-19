import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from conftest import KEY, persona_payload, prepare
from fastapi.testclient import TestClient

from virtual_you.backend.app import create_app
from virtual_you.backend.assistant import judgment_reason
from virtual_you.backend.providers import DemoProvider
from virtual_you.contracts.reporting import RetrievalRequest


def ask(
    client, question="What changed in the ingestion pipeline?", request_id="question-1", **extra
):
    return client.post(
        "/api/assistant/questions",
        json={
            "request_id": request_id,
            "question": question,
            "recipient_id": "manager",
            "destination": {"target": "demo-channel"},
            **extra,
        },
    )


def seed(client, record, hours=0):
    stamp = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    record["timestamp_range"] = {"started_at": stamp, "ended_at": stamp}
    record["end_state"] = (
        "Ingestion pipeline validation completed. Updated files and callback handling. Commit abcdef123 recorded."
    )
    prepare(client, record)


@pytest.mark.parametrize(
    "question",
    [
        "Which files changed?",
        "Can you explain what changed in the ingestion pipeline?",
        "What changed in this commit?",
        "How does ingestion work?",
    ],
)
def test_factual_questions_are_not_blocked_by_phrase_whitelist(client, record, question):
    assert judgment_reason(question) is None
    seed(client, record)
    response = ask(client, question)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "draft_ready", result
    draft = client.get("/api/drafts/" + result["draft_id"]).json()
    assert draft["kind"] == "reply" and draft["status"] == "pending"
    assert draft["paragraphs"] and draft["evidence"]
    assert "Starting state\n" not in draft["text"]


@pytest.mark.parametrize(
    "question",
    [
        "Should we change scope?",
        "Can you deploy it?",
        "Can you promise delivery?",
        "When will you finish?",
        "What do you think of my manager?",
    ],
)
def test_judgment_escalates_without_model_or_delivery(client, record, question):
    seed(client, record)

    class Forbidden(DemoProvider):
        async def generate(self, **kwargs):
            pytest.fail("Judgment questions must not ask the model to decide")

    client.app.state.engine.provider = Forbidden()
    result = ask(client, question).json()
    assert result["status"] == "escalated"
    assert client.get("/api/drafts").json() == []


def test_missing_evidence_persists_for_owner_and_survives_restart(settings):
    with TestClient(create_app(settings)) as client:
        client.headers["Authorization"] = "Bearer " + KEY
        client.post("/api/personas", json=persona_payload())
        assert ask(client).json()["reason"] == "missing_evidence"
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/assistant/requests").status_code == 401
        client.headers["Authorization"] = "Bearer " + KEY
        assert len(client.get("/api/assistant/requests").json()) == 1
        resolved = client.post(
            "/api/assistant/requests/question-1/resolve",
            json={"note": "Handled password=private-value"},
        ).json()
        assert resolved["status"] == "resolved" and "private-value" not in str(resolved)
        assert client.get("/api/drafts").json() == []


def test_old_history_is_valid_for_historical_question_not_current_status(client, record):
    seed(client, record, hours=72)
    assert ask(client, "What changed in this commit?").json()["status"] == "draft_ready"
    assert (
        ask(client, "What is the current status?", "current").json()["reason"] == "stale_evidence"
    )


def test_approval_rechecks_evidence_and_scope(client, record):
    seed(client, record)
    result = ask(client).json()
    record["end_state"] = "Correction: not complete."
    client.post("/api/activities", json=record)
    response = client.post(
        f"/api/drafts/{result['draft_id']}/decision",
        json={"expected_revision": 1, "action": "approve"},
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "evidence_changed"


def test_duplicate_request_reuses_draft_and_rejects_content_change(client, record):
    seed(client, record)
    assert ask(client).json() == ask(client).json()
    assert len(client.get("/api/drafts").json()) == 1
    assert ask(client, "Different question").status_code == 409


def test_explicit_retrieval_filters_cannot_change_on_replay(client, record):
    seed(client, record)
    assert ask(client).json()["status"] == "draft_ready"
    response = ask(client, retrieval={"since": "2020-01-01T00:00:00Z"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "request_conflict"
    assert len(client.get("/api/drafts").json()) == 1


def test_cached_reply_cannot_bypass_a_new_blocked_policy(client, record):
    from virtual_you.contracts.assistant import QuestionRequest

    seed(client, record)
    assert ask(client).json()["status"] == "draft_ready"
    request = QuestionRequest(
        request_id="question-1",
        question="What changed in the ingestion pipeline?",
        recipient_id="manager",
        destination={"target": "demo-channel"},
    )
    result = asyncio.run(client.app.state.assistant.ask(request, blocked_reason="audience_changed"))
    assert result["status"] == "escalated" and result["reply"] is None
    assert result["reason"] == "audience_changed"


def test_unrelated_question_is_not_blocked_by_slow_generation(client):
    assistant = client.app.state.assistant

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def reply(**kwargs):
            if kwargs["question"] == "slow":
                entered.set()
                await release.wait()
            return {"evidence": [], "paragraphs": [], "text": "Unknown"}

        assistant.workflow.engine.reply = reply
        args = {"recipient_id": "manager", "scope": RetrievalRequest(), "style": {}}
        slow = asyncio.create_task(assistant.prepare("slow", question="slow", **args))
        await entered.wait()
        quick = await asyncio.wait_for(
            assistant.prepare("quick", question="quick", **args), timeout=1
        )
        assert quick["status"] == "escalated"
        release.set()
        await slow
        assert not assistant.locks.entries

    asyncio.run(scenario())


def test_audio_ingestion_does_not_add_unrelated_github_data(settings, monkeypatch):
    from test_voice import FakeSTT, confirmation, upload

    monkeypatch.setenv("VIRTUAL_YOU_MCP_GITHUB", "true")

    def forbidden(*args, **kwargs):
        pytest.fail("Voice must not inherit unrelated GitHub observations")

    monkeypatch.setattr("virtual_you.ingest.service.enrich", forbidden)
    with TestClient(create_app(settings, transcriber=FakeSTT())) as client:
        client.headers["Authorization"] = "Bearer " + KEY
        client.post("/api/personas", json=persona_payload())
        note = upload(client)
        response = client.post(f"/api/voice/{note['id']}/confirm", json=confirmation(note))
        assert response.status_code == 200, response.text
        assert response.json()["activity"]["tool_calls"] == []


def test_current_commit_uses_git_only_when_permitted(client, record):
    import copy
    seed(client, record)
    git = copy.deepcopy(record)
    git.update(session_id='git-current', source='git', end_state='Recorded commit abcdef1: Fix group replies.')
    retrieval = client.app.state.retrieval
    retrieval.upsert(git)
    for sources, expected in [(['claude', 'git'], {'git'}), (['claude'], {'claude'})]:
        reply = asyncio.run(client.app.state.engine.reply(
            question='hey how is the commit going?',
            scope=RetrievalRequest(sources=sources), style={},
        ))
        assert {e['source'] for e in reply['evidence']} == expected
        ids = {e['evidence_id'] for e in reply['evidence']}
        assert all(c['evidence_id'] in ids for p in reply['paragraphs'] for c in p['citations'])
