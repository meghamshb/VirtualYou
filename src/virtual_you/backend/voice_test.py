"""Explicit local-only audio diagnostics, separate from the authenticated review API."""

import asyncio
from ipaddress import ip_address
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Request

from virtual_you.backend.errors import ServiceError
from virtual_you.backend.voice import MAX_AUDIO_BYTES, MAX_AUDIO_SECONDS
from virtual_you.ingest.redact import redact_value


def require_local_test(request: Request):
    if not request.app.state.settings.voice_test_mode:
        raise HTTPException(404, "Audio test mode is not enabled.")
    try:
        local = request.client is not None and ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    if not local or request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(403, "Audio testing is available on this computer only.")
    if request.method == "POST" and (
        request.headers.get("origin") != str(request.base_url).rstrip("/")
        or request.headers.get("x-virtual-you-test") != "1"
        or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"
    ):
        raise HTTPException(403, "Open the local audio-test page to transcribe.")


router = APIRouter(prefix="/dev/voice", dependencies=[Depends(require_local_test)])


@router.get("/status")
def status(request: Request):
    settings = request.app.state.settings
    return {
        "provider": "elevenlabs:scribe_v2",
        "configured": bool(settings.elevenlabs_api_key) and settings.voice_provider == "elevenlabs",
        "max_bytes": MAX_AUDIO_BYTES,
        "max_seconds": MAX_AUDIO_SECONDS,
    }


@router.post("/transcribe")
async def transcribe(request: Request):
    settings, voice = request.app.state.settings, request.app.state.voice
    if settings.voice_provider != "elevenlabs":
        raise ServiceError("voice_test_provider", "Start the server with --voice-test.", 503)
    content = await request.body()
    started = perf_counter()
    # Reuse the voice lock, but do not create a voice note, activity or draft.
    async with voice.lock:
        result = await asyncio.to_thread(voice.transcriber.inspect, content)
    result["elapsed_seconds"] = round(perf_counter() - started, 2)
    result["credential_redaction"] = True
    return redact_value(result, extra_secrets=[settings.api_key, settings.elevenlabs_api_key])
