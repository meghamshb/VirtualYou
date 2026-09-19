import io
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

from virtual_you.backend.app import create_app
from virtual_you.backend.cli import main
from virtual_you.backend.config import Settings
from virtual_you.backend.voice import MAX_AUDIO_BYTES, ElevenLabsTranscriber

BASE = "http://127.0.0.1:8000"
HEADERS = {"Origin": BASE, "X-Virtual-You-Test": "1"}
PATH = "/dev/voice/transcribe"


def wav(seconds=0.1):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        writer.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buffer.getvalue()


@pytest.fixture
def tester(settings):
    pytest.importorskip("av")
    settings.voice_test_mode = True
    settings.voice_provider = "elevenlabs"
    settings.elevenlabs_api_key = "test-elevenlabs-credential"
    calls = []
    reply = {"status": 200, "body": {"text": "Hello", "language_code": "eng", "words": []}}

    def respond(request):
        calls.append(request)
        return httpx.Response(reply["status"], json=reply["body"])

    transcriber = ElevenLabsTranscriber(
        settings.elevenlabs_api_key, transport=httpx.MockTransport(respond)
    )
    app = create_app(settings, transcriber=transcriber)
    with TestClient(app, base_url=BASE, client=("127.0.0.1", 12345)) as client:
        yield client, calls, reply


def test_mode_is_opt_in_and_regular_api_stays_protected(settings, tester):
    client, calls, _ = tester
    assert "Audio test" in client.get("/").text
    assert "Backend API key" in client.get("/review").text
    assert "frame-ancestors 'none'" in client.get("/voice-test").headers["Content-Security-Policy"]
    status = client.get("/dev/voice/status").json()
    assert status["configured"] and settings.elevenlabs_api_key not in str(status)
    assert client.get("/api/status").status_code == 401
    assert (
        client.post("/api/voice?request_id=test", content=wav(), headers=HEADERS).status_code == 401
    )
    settings.voice_test_mode = False
    assert "Virtual You — Review" in client.get("/").text
    for path in ["/voice-test", "/dev/voice/status"]:
        assert client.get(path).status_code == 404
    assert client.post(PATH, content=wav(), headers=HEADERS).status_code == 404
    assert calls == []


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": BASE},
        {"X-Virtual-You-Test": "1"},
        {**HEADERS, "Origin": "null"},
        {**HEADERS, "Origin": "https://example.com"},
        {**HEADERS, "Origin": "http://localhost:8000"},
        {**HEADERS, "Sec-Fetch-Site": "cross-site"},
        {**HEADERS, "Host": "rebind.example.com:8000", "Origin": "http://rebind.example.com:8000"},
    ],
)
def test_browser_boundary_prevents_provider_calls(tester, headers):
    client, calls, _ = tester
    assert client.post(PATH, headers=headers, content=wav()).status_code == 403
    assert not calls


@pytest.mark.parametrize("peer", ["192.0.2.1", "testclient"])
def test_non_loopback_peer_is_rejected(tester, peer):
    client, calls, _ = tester
    # The fixture already owns the app lifespan and its single-worker lock.
    remote = TestClient(client.app, base_url=BASE, client=(peer, 12345))
    try:
        assert remote.post(PATH, headers=HEADERS, content=wav()).status_code == 403
    finally:
        remote.close()
    assert not calls


@pytest.mark.parametrize("status", [200, 401, 403, 429, 500])
def test_provider_response_is_visible_redacted_and_has_no_saved_side_effects(
    settings, tester, status
):
    client, calls, reply = tester
    reply["status"] = status
    reply["body"] = {
        "text": "Visible transcript " + settings.elevenlabs_api_key,
        "words": [{"text": "hello", "start": 0, "end": 0.1, "type": "word"}],
        "detail": {"status": "provider_detail", "message": settings.api_key},
        "language_code": "eng",
    }
    response = client.post(PATH, headers=HEADERS, content=wav())
    assert response.status_code == 200
    result = response.json()
    assert result["provider_http_status"] == status
    assert result["response"]["words"] == reply["body"]["words"]
    assert result["response"]["language_code"] == "eng"
    assert result["duration_seconds"] == 0.1
    assert result["elapsed_seconds"] >= 0
    assert settings.elevenlabs_api_key not in response.text
    assert settings.api_key not in response.text
    assert len(calls) == 1
    assert client.app.state.voice.list_notes() == []
    assert client.app.state.store.list_drafts() == []
    assert client.app.state.retrieval.stats()["count"] == 0


@pytest.mark.parametrize(
    "content,status", [(b"", 413), (b"junk", 422), (b"x" * (MAX_AUDIO_BYTES + 1), 413)]
)
def test_invalid_audio_never_reaches_provider(tester, content, status):
    client, calls, _ = tester
    assert client.post(PATH, headers=HEADERS, content=content).status_code == status
    assert not calls


def test_non_json_provider_error_is_visible_and_bounded(tester):
    client, _, _ = tester
    client.app.state.voice.transcriber.transport = httpx.MockTransport(
        lambda request: httpx.Response(502, text="upstream error " * 1000)
    )
    result = client.post(PATH, headers=HEADERS, content=wav()).json()
    assert result["provider_http_status"] == 502
    assert result["response"].startswith("upstream error")
    assert len(result["response"]) == 8000


def test_network_error_preserves_generic_error_without_secret(tester):
    client, _, _ = tester

    def fail(request):
        raise httpx.ConnectError("private request headers", request=request)

    client.app.state.voice.transcriber.transport = httpx.MockTransport(fail)
    response = client.post(PATH, headers=HEADERS, content=wav())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "transcription_failed"
    assert "private request headers" not in response.text


@pytest.mark.parametrize("args", [["--host", "0.0.0.0"], ["show-key"], ["seed-demo"]])
def test_cli_rejects_nonlocal_or_nonserve_test_mode(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["virtual-you-server", *args, "--voice-test"])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 2


def test_cli_explicitly_enables_elevenlabs_without_proxy_header_trust(settings, monkeypatch):
    import uvicorn

    captured = {}
    monkeypatch.setattr("sys.argv", ["virtual-you-server", "serve", "--voice-test"])
    monkeypatch.setattr(Settings, "from_env", lambda: settings)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: captured.update(kwargs))
    main()
    assert settings.voice_test_mode and settings.voice_provider == "elevenlabs"
    assert captured["host"] == "127.0.0.1" and captured["proxy_headers"] is False
