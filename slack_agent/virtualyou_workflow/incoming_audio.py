"""Verified incoming personal-DM audio → question text, never activity ingestion."""

import asyncio
import os
import re
from pathlib import PurePath
from urllib.parse import urlparse

import httpx
from virtual_you.backend.errors import ServiceError
from virtual_you.backend.voice import MAX_AUDIO_BYTES
from virtual_you.ingest.redact import assert_safe_serialized, is_sensitive_key, redact_text

from .history import slack_call

AUDIO_EXTENSIONS = {".m4a", ".webm", ".wav", ".mp3", ".ogg", ".opus", ".aac", ".aiff", ".aif", ".flac"}


def _audio_file(file):
    mime = str(file.get("mimetype") or "").lower().split(";", 1)[0]
    extension = PurePath(str(file.get("name") or "")).suffix.lower()
    native_audio = file.get("subtype") == "slack_audio" or file.get("audio_display_type") == "waveform"
    return mime.startswith("audio/") or extension in AUDIO_EXTENSIONS or (
        mime == "video/mp4" and native_audio
    )


def _shared_in(file, channel):
    ims = file.get("ims")
    if isinstance(ims, list) and channel in ims:
        return True
    shares = file.get("shares")
    if not isinstance(shares, dict):
        return False
    return any(
        isinstance(shares.get(kind), dict) and bool(shares[kind].get(channel))
        for kind in ("private", "public")
    )


async def _attached_to_message(client, row, file_id):
    ts = row.get("source_ts")
    if not isinstance(ts, str) or not re.fullmatch(r"\d+\.\d{1,6}", ts):
        return False
    history = await asyncio.to_thread(
        slack_call, client.conversations_history, channel=row["channel"],
        oldest=ts, latest=ts, inclusive=True, limit=1,
    )
    return any(
        message.get("ts") == ts and message.get("user") == row["recipient"]
        and not message.get("bot_id")
        and any(isinstance(file, dict) and file.get("id") == file_id for file in (message.get("files") or []))
        for message in history.get("messages", []) if isinstance(message, dict)
    )


async def _download(file, token):
    url = file.get("url_private_download") or file.get("url_private") or ""
    try:
        parsed = urlparse(url)
        valid = (
            parsed.scheme == "https" and parsed.hostname == "files.slack.com"
            and not parsed.username and not parsed.password and parsed.port in (None, 443)
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ServiceError("invalid_voice_url", "Slack did not provide a supported private recording URL.", 422)
    try:
        size = int(file.get("size") or 0)
    except (ValueError, TypeError):
        raise ServiceError("invalid_audio_size", "The recording size is invalid.", 422) from None
    if size < 0 or size > MAX_AUDIO_BYTES:
        raise ServiceError("invalid_audio_size", "Choose one audio recording up to 8 MiB.", 413)
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as http:
            async with http.stream("GET", url, headers={"Authorization": "Bearer " + token}) as response:
                # Redirects are not followed and never receive the owner's credential.
                response.raise_for_status()
                declared = response.headers.get("content-length")
                if declared and int(declared) > MAX_AUDIO_BYTES:
                    raise ServiceError("invalid_audio_size", "Choose one audio recording up to 8 MiB.", 413)
                content = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    if len(content) + len(chunk) > MAX_AUDIO_BYTES:
                        raise ServiceError("invalid_audio_size", "Choose one audio recording up to 8 MiB.", 413)
                    content.extend(chunk)
        if not content:
            raise ServiceError("invalid_audio_size", "The recording is empty.", 422)
        return bytes(content)
    except (httpx.HTTPError, ValueError):
        raise ServiceError("voice_download_failed", "Could not read the recording. Check Slack file access and retry.", 502) from None


async def transcribe_incoming_audio(c, row, file_ids) -> str:
    """Authorize one sender-owned attachment, transcribe in memory, and redact its text."""
    if (
        not isinstance(file_ids, (list, tuple)) or len(file_ids) != 1
        or not isinstance(file_ids[0], str) or not re.fullmatch(r"F[A-Z0-9]+", file_ids[0])
    ):
        raise ServiceError("unsupported_voice_file", "Send one audio recording at a time.", 422)
    if not row.get("channel") or not row.get("recipient") or row["recipient"] == c.config.owner_id:
        raise ServiceError("not_owner_dm", "This recording is not from the selected colleague DM.", 403)
    token = c.credentials.user_token()
    if not token:
        raise ServiceError("user_connection_required", "Reconnect your Slack account before transcribing.", 403)
    client = c.client_factory(token=token, timeout=15, retry_handlers=[])
    identity = await asyncio.to_thread(slack_call, client.auth_test)
    if (
        identity.get("user_id") != c.config.owner_id
        or identity.get("team_id") != c.config.team_id or identity.get("bot_id")
    ):
        raise ServiceError("wrong_user_token", "Reconnect the configured Slack owner.", 403)
    info = await asyncio.to_thread(slack_call, client.conversations_info, channel=row["channel"])
    channel = info.get("channel")
    if not isinstance(channel, dict) or not channel.get("is_im") or channel.get("user") != row["recipient"]:
        raise ServiceError("not_owner_dm", "This recording is not in the selected colleague DM.", 403)
    payload = await asyncio.to_thread(slack_call, client.files_info, file=file_ids[0])
    file = payload.get("file")
    if not isinstance(file, dict) or file.get("id") != file_ids[0] or file.get("user") != row["recipient"]:
        raise ServiceError("voice_file_author_mismatch", "The recording must belong to this DM's sender.", 403)
    if not _audio_file(file):
        raise ServiceError("unsupported_voice_file", "Send an audio recording, not a video or other document.", 422)
    if not _shared_in(file, row["channel"]) and not await _attached_to_message(client, row, file_ids[0]):
        raise ServiceError("voice_file_channel_mismatch", "The recording could not be verified in this incoming message.", 403)
    content = await _download(file, token)
    try:
        result = await asyncio.to_thread(c.backend.voice.transcriber.transcribe, content)
    except ServiceError:
        raise
    except Exception:
        raise ServiceError("transcription_failed", "The recording could not be transcribed. Try a new recording.", 503) from None
    finally:
        del content
    transcript = result.get("transcript") if isinstance(result, dict) else None
    if not isinstance(transcript, str) or not transcript.strip():
        raise ServiceError("no_speech", "No clear speech was detected in the recording.", 422)
    secrets = [token, *getattr(c.backend.voice, "secrets", ())]
    secrets.extend(value for key, value in os.environ.items() if is_sensitive_key(key) and value)
    transcript = redact_text(transcript, secrets).strip()
    if len(transcript) > 4000:
        raise ServiceError("invalid_transcript", "The spoken question is too long. Send a shorter recording.", 422)
    assert_safe_serialized(transcript, extra_secrets=secrets)
    return transcript
