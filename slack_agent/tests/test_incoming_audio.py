import asyncio
from types import SimpleNamespace

import httpx
import pytest
from virtual_you.backend.errors import ServiceError
from virtual_you.backend.voice import MAX_AUDIO_BYTES

from virtualyou_workflow import incoming_audio

ROW = {"recipient": "UFRIEND", "channel": "DHUMAN", "source_ts": "1700000000.123456"}


@pytest.fixture
def setup_audio(monkeypatch):
    http_requests, transcribed, slack_calls = [], [], []
    file = {
        "id": "F123", "user": "UFRIEND", "name": "question.m4a", "mimetype": "audio/mp4",
        "size": 4, "ims": ["DHUMAN"], "url_private": "https://files.slack.com/files-pri/T-F123/audio",
    }
    identity = {"user_id": "UOWNER", "team_id": "TTEAM"}
    channel = {"is_im": True, "user": "UFRIEND"}
    history = {"messages": [{"ts": ROW["source_ts"], "user": "UFRIEND", "files": [{"id": "F123"}]}]}
    transcript = {"transcript": "What changed in the callback?"}

    class Slack:
        def auth_test(self):
            slack_calls.append("auth")
            return identity

        def conversations_info(self, **kwargs):
            assert kwargs == {"channel": "DHUMAN"}
            slack_calls.append("channel")
            return {"channel": channel}

        def files_info(self, **kwargs):
            assert kwargs == {"file": "F123"}
            slack_calls.append("file")
            return {"file": file}

        def conversations_history(self, **kwargs):
            assert kwargs == {"channel": "DHUMAN", "oldest": ROW["source_ts"], "latest": ROW["source_ts"], "inclusive": True, "limit": 1}
            slack_calls.append("history")
            return history

    def forbidden(*args, **kwargs):
        pytest.fail("Incoming questions must not be persisted as voice notes or work evidence")

    def transcribe(content):
        transcribed.append(content)
        return transcript

    def factory(**kwargs):
        assert kwargs["token"] == "owner-test-token"
        return Slack()

    c = SimpleNamespace(
        config=SimpleNamespace(owner_id="UOWNER", team_id="TTEAM"),
        credentials=SimpleNamespace(user_token=lambda: "owner-test-token"),
        client_factory=factory,
        backend=SimpleNamespace(
            voice=SimpleNamespace(transcriber=SimpleNamespace(transcribe=transcribe), secrets=("local-secret-value",), upload=forbidden, save=forbidden),
            retrieval=SimpleNamespace(upsert=forbidden), store=SimpleNamespace(connection=forbidden),
        ),
    )
    response = {"status": 200, "content": b"WAVE", "headers": {}}

    def download(request):
        http_requests.append(request)
        assert request.url.host == "files.slack.com"
        assert request.headers["Authorization"] == "Bearer owner-test-token"
        return httpx.Response(response["status"], content=response["content"], headers=response["headers"])

    real_client = httpx.AsyncClient

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        return real_client(**kwargs, transport=httpx.MockTransport(download))

    monkeypatch.setattr(incoming_audio.httpx, "AsyncClient", client)
    return SimpleNamespace(c=c, file=file, identity=identity, channel=channel, history=history,
                           transcript=transcript, response=response, http=http_requests,
                           transcribed=transcribed, slack=slack_calls)


def run(setup, file_ids=None):
    return asyncio.run(incoming_audio.transcribe_incoming_audio(setup.c, dict(ROW), ["F123"] if file_ids is None else file_ids))


def test_transcribes_verified_audio_in_memory_without_indexing_and_redacts_known_values(setup_audio, monkeypatch):
    setup = setup_audio
    monkeypatch.setenv("SOME_PRIVATE_API_KEY", "environment-secret-value")
    setup.transcript["transcript"] = "Question with local-secret-value environment-secret-value owner-test-token"
    assert run(setup) == "Question with [REDACTED] [REDACTED] [REDACTED]"
    assert setup.transcribed == [b"WAVE"] and len(setup.http) == 1
    assert setup.slack == ["auth", "channel", "file"]


@pytest.mark.parametrize("kind", ["private", "public"])
def test_explicit_file_share_proves_channel_without_history(setup_audio, kind):
    setup = setup_audio
    setup.file["ims"] = []
    setup.file["shares"] = {kind: {"DHUMAN": [{"ts": ROW["source_ts"]}]}}
    assert run(setup) == setup.transcript["transcript"]
    assert "history" not in setup.slack


def test_missing_share_metadata_requires_exact_original_message_attachment(setup_audio):
    setup = setup_audio
    setup.file.pop("ims")
    assert run(setup) == setup.transcript["transcript"]
    assert setup.slack[-1] == "history"


@pytest.mark.parametrize("mutation,code", [
    (lambda s: s.identity.update(user_id="UOTHER"), "wrong_user_token"),
    (lambda s: s.identity.update(team_id="TOTHER"), "wrong_user_token"),
    (lambda s: s.identity.update(bot_id="BBOT"), "wrong_user_token"),
    (lambda s: s.channel.update(is_im=False), "not_owner_dm"),
    (lambda s: s.channel.update(user="UOTHER"), "not_owner_dm"),
    (lambda s: s.file.update(user="UOTHER"), "voice_file_author_mismatch"),
    (lambda s: s.file.update(id="FOTHER"), "voice_file_author_mismatch"),
    (lambda s: s.file.update(mimetype="video/mp4", name="movie.mp4"), "unsupported_voice_file"),
    (lambda s: s.file.update(mimetype="application/pdf", name="doc.pdf"), "unsupported_voice_file"),
    (lambda s: s.file.update(size=MAX_AUDIO_BYTES + 1), "invalid_audio_size"),
])
def test_rejected_identity_scope_type_and_declared_size_never_download(setup_audio, mutation, code):
    setup = setup_audio
    mutation(setup)
    with pytest.raises(ServiceError) as error:
        run(setup)
    assert error.value.code == code
    assert not setup.http and not setup.transcribed


@pytest.mark.parametrize("url", [
    "https://evil.test/audio", "https://files.slack.com.evil.test/audio",
    "http://files.slack.com/audio", "https://person:password@files.slack.com/audio",
    "https://files.slack.com:444/audio",
])
def test_owner_token_only_goes_to_exact_https_slack_file_host(setup_audio, url):
    setup = setup_audio
    setup.file["url_private"] = url
    with pytest.raises(ServiceError) as error:
        run(setup)
    assert error.value.code == "invalid_voice_url"
    assert not setup.http and not setup.transcribed


@pytest.mark.parametrize("message", [
    {"ts": "1700000000.123455", "user": "UFRIEND", "files": [{"id": "F123"}]},
    {"ts": ROW["source_ts"], "user": "UOTHER", "files": [{"id": "F123"}]},
    {"ts": ROW["source_ts"], "user": "UFRIEND", "files": [{"id": "FOTHER"}]},
])
def test_wrong_channel_or_attachment_does_not_authorize_download(setup_audio, message):
    setup = setup_audio
    setup.file["ims"] = ["DOTHER"]
    setup.history["messages"] = [message]
    with pytest.raises(ServiceError) as error:
        run(setup)
    assert error.value.code == "voice_file_channel_mismatch"
    assert not setup.http and not setup.transcribed


def test_redirect_is_not_followed_and_stream_size_is_enforced(setup_audio):
    setup = setup_audio
    setup.response.update(status=302, headers={"Location": "https://evil.test/steal"})
    with pytest.raises(ServiceError) as error:
        run(setup)
    assert error.value.code == "voice_download_failed"
    assert len(setup.http) == 1 and not setup.transcribed
    # A lying Content-Length must not bypass the streaming limit.
    setup.response.update(status=200, headers={"Content-Length": "4"}, content=b"a" * (MAX_AUDIO_BYTES + 1))
    with pytest.raises(ServiceError) as error:
        run(setup)
    assert error.value.code == "invalid_audio_size"
    assert not setup.transcribed


def test_native_audio_container_requires_explicit_audio_marker(setup_audio):
    setup = setup_audio
    setup.file.update(mimetype="video/mp4", name="clip.mp4", subtype="slack_audio")
    assert run(setup) == setup.transcript["transcript"]


@pytest.mark.parametrize("text,code", [("", "no_speech"), ("  ", "no_speech"), ("x" * 4001, "invalid_transcript")])
def test_empty_or_overlong_transcript_fails_safely(setup_audio, text, code):
    setup = setup_audio
    setup.transcript["transcript"] = text
    with pytest.raises(ServiceError) as error:
        run(setup)
    assert error.value.code == code


@pytest.mark.parametrize("ids", [[], ["F123", "F456"], "F123", ["../../private"]])
def test_accepts_exactly_one_slack_file_id(setup_audio, ids):
    with pytest.raises(ServiceError) as error:
        run(setup_audio, ids)
    assert error.value.code == "unsupported_voice_file"
    assert not setup_audio.slack and not setup_audio.http
