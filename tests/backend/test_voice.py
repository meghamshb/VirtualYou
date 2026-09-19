import io
import json
import wave

import httpx
import pytest
from conftest import KEY, persona_payload
from fastapi.testclient import TestClient

from virtual_you.backend.app import create_app
from virtual_you.backend.errors import ServiceError
from virtual_you.backend.providers import DemoProvider
from virtual_you.backend.voice import (
    MAX_AUDIO_BYTES,
    ElevenLabsTranscriber,
    decode_audio,
    make_transcriber,
)


class FakeSTT:
    def __init__(self):
        self.calls = 0

    def transcribe(self, content):
        self.calls += 1
        return {
            "transcript": "Completed the callback. password=private-value",
            "language": "en",
            "duration_seconds": 5,
            "provider": "test:stub",
        }


def upload(client, **kwargs):
    response = client.post(
        "/api/voice?request_id=memo-1", content=b"synthetic-stub-audio", **kwargs
    )
    assert response.status_code == 201, response.text
    return response.json()


def confirmation(note, **kwargs):
    return {
        "expected_revision": note["revision"],
        "recipient_id": "manager",
        "project": "Project A",
        "transcript": "Completed callback validation; three tests passed (user report).",
        "destination": {"target": "demo-channel"},
        **kwargs,
    }


def test_voice_review_redacts_and_confirm_uses_normalized_evidence(settings):
    transcriber = FakeSTT()
    with TestClient(create_app(settings, transcriber=transcriber)) as client:
        client.headers["Authorization"] = "Bearer " + KEY
        client.post("/api/personas", json=persona_payload())
        note = upload(client)
        assert note["status"] == "needs_review" and "private-value" not in str(note)
        assert client.app.state.retrieval.stats()["count"] == 0
        assert upload(client) == note and transcriber.calls == 1
        assert client.post("/api/voice?request_id=memo-1", content=b"different").status_code == 409
        edit = client.post(
            f"/api/voice/{note['id']}/edit",
            json={"expected_revision": 1, "transcript": "Corrected: not deployed."},
        ).json()
        assert edit["revision"] == 2
        assert (
            client.post(
                f"/api/voice/{note['id']}/edit", json={"expected_revision": 1, "transcript": "old"}
            ).status_code
            == 409
        )
        payload = confirmation(edit)
        response = client.post(f"/api/voice/{note['id']}/confirm", json=payload)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] == "draft_ready"
        activity = result["activity"]
        assert activity["source"] == "voice" and activity["redacted"] is True
        assert "not independently verified" in json.dumps(activity)
        assert not activity["files_changed"] and not activity["diffs"]
        draft = client.get("/api/drafts/" + result["draft_id"]).json()
        assert draft["status"] == "pending" and draft["approval"] is None
        assert "Voice evidence is user-reported" in str(draft["warnings"])
        assert draft["request"]["retrieval"]["project_ids"] == ["Project A"]
        assert client.post(f"/api/voice/{note['id']}/confirm", json=payload).json() == result
        assert client.app.state.retrieval.stats()["count"] == 1
        assert (
            client.post(
                f"/api/voice/{note['id']}/confirm", json=confirmation(edit, project="Other")
            ).status_code
            == 409
        )
        assert "private-value" not in json.dumps(client.get("/api/voice").json())
        assert not list(settings.data_dir.rglob("*.wav"))


def test_failed_voice_generation_resumes_same_confirmation_after_restart(settings):
    class Broken(DemoProvider):
        async def generate(self, **kwargs):
            raise ServiceError("model_unavailable", "Try again", 503)

    with TestClient(create_app(settings, transcriber=FakeSTT())) as client:
        client.headers["Authorization"] = "Bearer " + KEY
        client.post("/api/personas", json=persona_payload())
        note = upload(client)
        client.app.state.engine.provider = Broken()
        payload = confirmation(note)
        assert client.post(f"/api/voice/{note['id']}/confirm", json=payload).status_code == 503
        saved = client.get(f"/api/voice/{note['id']}").json()
        assert (
            saved["status"] == "confirmed"
            and saved["confirmed_request"]["transcript"] == payload["transcript"]
        )
    with TestClient(create_app(settings, transcriber=FakeSTT())) as client:
        client.headers["Authorization"] = "Bearer " + KEY
        result = client.post(f"/api/voice/{note['id']}/confirm", json=saved["confirmed_request"])
        assert result.status_code == 200, result.text
        assert client.app.state.retrieval.stats()["count"] == 1
        assert len(client.get("/api/drafts").json()) == 1


def wav(seconds=0.1):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        writer.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buffer.getvalue()


def test_audio_bounds_and_decode():
    pytest.importorskip("av")
    assert len(decode_audio(wav())) == 1600
    for content, code in [
        (b"junk", "invalid_audio"),
        (wav(181), "audio_too_long"),
        (b"", "invalid_audio_size"),
    ]:
        with pytest.raises(ServiceError) as caught:
            decode_audio(content)
        assert caught.value.code == code


def test_upload_size_and_auth_fail_before_transcription(settings):
    transcriber = FakeSTT()
    with TestClient(create_app(settings, transcriber=transcriber)) as client:
        assert client.post("/api/voice?request_id=x", content=b"x").status_code == 401
        client.headers["Authorization"] = "Bearer " + KEY
        assert (
            client.post("/api/voice?request_id=x", content=b"x" * (MAX_AUDIO_BYTES + 1)).status_code
            == 413
        )
        assert client.post("/api/voice?request_id=x", content=b"").status_code == 413
        assert client.post("/api/voice?request_id=../../x", content=b"x").status_code == 422
        assert transcriber.calls == 0


def test_cloud_stt_is_explicit_and_uses_scribe_multipart(settings):
    pytest.importorskip("av")
    assert make_transcriber(settings).__class__.__name__ == "LocalWhisper"
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://api.elevenlabs.io/v1/speech-to-text"
        assert request.headers["xi-api-key"] == "test-credential"
        assert b"scribe_v2" in request.content and b"memo.audio" in request.content
        return httpx.Response(200, json={"text": "A test transcript", "language_code": "en"})

    result = ElevenLabsTranscriber(
        "test-credential", transport=httpx.MockTransport(handler)
    ).transcribe(wav())
    assert (
        result["transcript"] == "A test transcript" and result["provider"] == "elevenlabs:scribe_v2"
    )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "status,body,code",
    [
        (401, {}, "voice_auth_failed"),
        (403, {}, "voice_auth_failed"),
        (429, {}, "voice_rate_limited"),
        (500, {}, "transcription_failed"),
        (200, [], "transcription_failed"),
        (200, {"text": ""}, "no_speech"),
    ],
)
def test_cloud_errors_are_bounded_and_do_not_retry(status, body, code):
    pytest.importorskip("av")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json=body)

    with pytest.raises(ServiceError) as caught:
        ElevenLabsTranscriber("test-credential", transport=httpx.MockTransport(handler)).transcribe(
            wav()
        )
    assert caught.value.code == code and len(calls) == 1
    assert "test-credential" not in str(caught.value)


def test_missing_cloud_key_never_uploads():
    with pytest.raises(ServiceError) as caught:
        ElevenLabsTranscriber("").transcribe(b"test")
    assert caught.value.code == "voice_key_required"


def test_configured_backend_key_is_redacted_from_transcript(settings):
    class KeySTT(FakeSTT):
        def transcribe(self, content):
            return {
                **super().transcribe(content),
                "transcript": "Spoken secret " + settings.api_key,
            }

    with TestClient(create_app(settings, transcriber=KeySTT())) as client:
        client.headers["Authorization"] = "Bearer " + KEY
        note = upload(client)
        assert settings.api_key not in note["transcript"]
        assert "[REDACTED]" in note["transcript"]
