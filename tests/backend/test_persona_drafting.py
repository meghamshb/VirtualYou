import json

import pytest
from conftest import KEY, new_draft, persona_payload, prepare
from fastapi.testclient import TestClient

from virtual_you.backend.app import create_app
from virtual_you.backend.providers import UNKNOWN, DemoProvider


def test_persona_has_private_markdown_and_five_sanitized_examples(client, settings):
    payload = persona_payload()
    payload["messages"][0] += " API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    response = client.post("/api/personas", json=payload)
    assert response.status_code == 201
    profile = response.json()
    assert len(profile["examples"]) == 5
    assert "sk-proj-" not in response.text
    assert "[REDACTED]" in profile["examples"][0]
    path = next((settings.data_dir / "personas").glob("*/soul.md"))
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.read_text() == profile["soul_md"]
    assert client.get("/api/personas/manager/soul").text == profile["soul_md"]


@pytest.mark.parametrize("count", [21])
def test_persona_caps_examples_at_twenty(client, count):
    payload = persona_payload()
    payload["messages"] = ["hello"] * count
    assert client.post("/api/personas", json=payload).status_code == 422


def test_profile_updates_increment_version(client):
    first = client.post("/api/personas", json=persona_payload()).json()
    second = client.post("/api/personas", json=persona_payload()).json()
    assert (first["version"], second["version"]) == (1, 2)


def test_two_personas_change_style_without_changing_factual_report(client, record):
    prepare(client, record)
    client.post("/api/personas", json=persona_payload("friend", formal=False))
    formal = new_draft(client)
    casual = new_draft(client, recipient_id="friend")
    assert formal["report"] == casual["report"]
    assert formal["text"] != casual["text"]
    assert "Hello," in formal["text"] and "Hey," in casual["text"]
    assert set(formal["report"]) == {
        "starting_state",
        "approach",
        "changes",
        "result",
        "links",
        "blockers",
    }
    assert formal["report"]["approach"][
        "citations"
    ]  # phase 1.1 records public assistant approach summaries
    approach = formal["report"]["approach"]
    assert approach["text"].startswith("I will inspect the callback and its tests.")
    assert "Reasoning occurred" not in approach["text"]
    assert approach["citations"][0]["quote"] in record["reasoning_summary"]
    assert "No blockers" not in formal["text"]
    assert "modified: src/payments/callback.py" in formal["text"]
    assert "FileOperation" not in formal["text"]


def test_prompt_separates_style_examples_and_evidence(settings, record):
    class Capturing(DemoProvider):
        async def generate(self, **kwargs):
            if kwargs["task"] == "draft":
                self.captured = kwargs
            return await super().generate(**kwargs)

    provider = Capturing()
    record["source"] = "codex"
    record["source_path"] = "/private/editor-history/codex-session.jsonl"
    with TestClient(create_app(settings, provider=provider)) as client:
        client.headers["Authorization"] = "Bearer " + KEY
        prepare(client, record)
        new_draft(client)
        payload = json.loads(provider.captured["user"])
        assert "style_examples_not_facts" in payload
        assert payload["evidence"][0]["session_id"] == record["session_id"]
        assert "ONLY factual source" in provider.captured["system"]
        assert all(item["source"] == "codex" for item in payload["evidence"])
        assert record["source_path"] not in provider.captured["user"]
        assert "source_path" not in provider.captured["user"]
        stored = client.post("/api/retrieval/search", json={}).json()["matches"][0]["record"]
        assert stored["source_path"] == record["source_path"]


@pytest.mark.parametrize(
    "marker",
    [
        "Reasoning occurred; omitted.",
        "Assistant reasoning was present in 1 block; detailed chain-of-thought is intentionally omitted.",
    ],
)
def test_omission_marker_alone_is_not_an_approach(client, record, marker):
    record["reasoning_summary"] = marker
    prepare(client, record)
    draft = new_draft(client)
    assert draft["report"]["approach"] == {"text": UNKNOWN, "citations": []}
    assert any("Private reasoning was omitted" in warning for warning in draft["warnings"])


@pytest.mark.parametrize(
    "failure,code",
    [
        ("missing", "unsupported_claim"),
        ("quote", "invalid_citation"),
        ("id", "invalid_citation"),
        ("url", "invented_link"),
        ("shape", "invalid_report"),
    ],
)
def test_generation_rejects_missing_or_fabricated_citations(settings, record, failure, code):
    class BadProvider(DemoProvider):
        async def generate(self, **kwargs):
            data = await super().generate(**kwargs)
            if kwargs["task"] == "draft":
                if failure == "missing":
                    data["result"]["citations"] = []
                elif failure == "quote":
                    data["result"]["citations"][0]["quote"] = "All production systems deployed"
                elif failure == "id":
                    data["result"]["citations"][0]["evidence_id"] = "invented"
                elif failure == "url":
                    data["result"]["text"] += " https://invented.example/pr/7"
                else:
                    del data["result"]
            return data

    with TestClient(create_app(settings, provider=BadProvider())) as client:
        client.headers["Authorization"] = "Bearer " + KEY
        prepare(client, record)
        result = client.post(
            "/api/drafts",
            json={"recipient_id": "manager", "destination": {"target": "demo-channel"}},
        )
        assert result.status_code == 502
        assert result.json()["error"]["code"] == code
        assert client.get("/api/drafts").json() == []


def test_no_matching_evidence_is_not_filled_with_unrelated_activity(client, record):
    prepare(client, record)
    result = client.post(
        "/api/drafts",
        json={
            "recipient_id": "manager",
            "retrieval": {"query": "spaceship"},
            "destination": {"target": "demo-channel"},
        },
    )
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "nothing_to_report"


@pytest.mark.parametrize("count", [0, 1, 9, 10, 20])
def test_sparse_history_is_formal_and_ten_messages_resume_inference(client, count):
    payload = persona_payload(formal=False)
    payload["messages"] = ["Hey! yep, cheers!"] * count
    response = client.post("/api/personas", json=payload)
    assert response.status_code == 201
    profile = response.json()
    assert profile["seed_message_count"] == count
    assert profile["style"]["formality"] == ("formal" if count < 10 else "casual")
    assert f"Owner-authored messages used: {count}." in profile["soul_md"]
