"""Local or explicit cloud transcription, review, and Member 1 → Member 3 handoff."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import threading
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import NAMESPACE_URL, uuid5

import httpx

from virtual_you.backend.errors import ServiceError
from virtual_you.backend.retrieval import canonical_hash
from virtual_you.contracts.activity import ActivityRecord
from virtual_you.contracts.reporting import DraftRequest, utcnow
from virtual_you.ingest.redact import assert_safe_serialized, redact_text
from virtual_you.ingest.service import IngestionService
from virtual_you.ingest.store import ActivityRecordRepository

MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_AUDIO_SECONDS = 180


def decode_audio(content):
    try:
        import av
        import numpy as np
    except ImportError:
        raise ServiceError(
            "voice_not_installed", "Install the voice or voice-cloud extra for audio decoding.", 503
        ) from None
    if not content or len(content) > MAX_AUDIO_BYTES:
        raise ServiceError("invalid_audio_size", "Choose an audio file up to 8 MiB.", 413)
    # Decode incrementally; metadata alone cannot enforce the duration limit.
    try:
        chunks, size = [], 0
        with av.open(io.BytesIO(content)) as container:
            if not container.streams.audio:
                raise ValueError("No audio stream")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            for frame in container.decode(audio=0):
                for part in resampler.resample(frame):
                    data = part.to_ndarray().reshape(-1)
                    size += len(data)
                    if size > MAX_AUDIO_SECONDS * 16000:
                        raise ServiceError(
                            "audio_too_long", "Voice memos must be three minutes or shorter.", 413
                        )
                    chunks.append(data)
            for part in resampler.resample(None):
                chunks.append(part.to_ndarray().reshape(-1))
        audio = np.concatenate(chunks).astype(np.float32) / 32768.0
        if not len(audio) or len(audio) > MAX_AUDIO_SECONDS * 16000:
            raise ServiceError("audio_too_long", "Choose a nonempty memo up to three minutes.", 413)
    except ServiceError:
        raise
    except Exception:
        raise ServiceError(
            "invalid_audio", "The file could not be decoded as audio.", 422
        ) from None
    return audio


class LocalWhisper:
    """Weights may be downloaded; audio is decoded and transcribed on this machine."""

    def __init__(self, model="small", language=None):
        self.model_name, self.language = model, language
        self.model = None
        self.lock = threading.Lock()

    def transcribe(self, content):
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise ServiceError(
                "voice_not_installed",
                "Install the project's voice extra to enable local transcription.",
                503,
            ) from None
        if not content or len(content) > MAX_AUDIO_BYTES:
            raise ServiceError("invalid_audio_size", "Choose an audio file up to 8 MiB.", 413)
        audio = decode_audio(content)
        try:
            with self.lock:
                if self.model is None:
                    self.model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
                segments, info = self.model.transcribe(
                    audio,
                    language=self.language,
                    vad_filter=True,
                    beam_size=5,
                    condition_on_previous_text=False,
                )
                text = " ".join(segment.text.strip() for segment in segments).strip()
            if not text:
                raise ServiceError(
                    "no_speech", "No clear speech was detected. Try another recording.", 422
                )
            return {
                "transcript": text,
                "duration_seconds": len(audio) / 16000,
                "language": info.language,
                "provider": "local:faster-whisper:" + self.model_name,
            }
        except ServiceError:
            raise
        except Exception:
            raise ServiceError(
                "transcription_failed",
                "Local transcription failed. Check the model installation and retry.",
                503,
            ) from None


class ElevenLabsTranscriber:
    """Explicit cloud choice. Raw audio is uploaded; there is no silent fallback."""

    def __init__(self, api_key, language=None, *, transport=None):
        self.api_key, self.language, self.transport = api_key, language, transport

    def transcribe(self, content):
        if not self.api_key:
            raise ServiceError(
                "voice_key_required",
                "Configure ELEVENLABS_API_KEY with Speech to Text permission.",
                503,
            )
        audio = decode_audio(content)
        data = {"model_id": "scribe_v2", "tag_audio_events": "false", "diarize": "false"}
        if self.language:
            data["language_code"] = self.language
        try:
            with httpx.Client(
                timeout=120, follow_redirects=False, transport=self.transport
            ) as client:
                response = client.post(
                    "https://api.elevenlabs.io/v1/speech-to-text",
                    headers={"xi-api-key": self.api_key},
                    data=data,
                    files={"file": ("memo.audio", content, "application/octet-stream")},
                )
                if response.status_code in {401, 403}:
                    raise ServiceError(
                        "voice_auth_failed",
                        "Check the ElevenLabs key, permissions and available allowance.",
                        503,
                    )
                if response.status_code == 429:
                    raise ServiceError(
                        "voice_rate_limited",
                        "ElevenLabs limited this request. Check allowance before retrying.",
                        503,
                    )
                response.raise_for_status()
                result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Invalid transcription response")
            if not isinstance(result.get("text"), str) or not result["text"].strip():
                raise ServiceError(
                    "no_speech", "No clear speech was detected. Try another recording.", 422
                )
            return {
                "transcript": result["text"],
                "duration_seconds": len(audio) / 16000,
                "language": result.get("language_code", "unknown"),
                "provider": "elevenlabs:scribe_v2",
            }
        except ServiceError:
            raise
        except (httpx.HTTPError, ValueError, TypeError):
            raise ServiceError(
                "transcription_failed",
                "ElevenLabs transcription failed. No automatic retry or provider switch was made.",
                503,
            ) from None


def make_transcriber(settings):
    if settings.voice_provider == "elevenlabs":
        return ElevenLabsTranscriber(settings.elevenlabs_api_key, settings.voice_language)
    return LocalWhisper(settings.voice_model, settings.voice_language)


class VoiceService:
    def __init__(self, settings, store, retrieval, workflow, transcriber):
        self.settings, self.store, self.retrieval = settings, store, retrieval
        self.workflow, self.transcriber = workflow, transcriber
        self.lock = asyncio.Lock()
        with store.connection(write=True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS voice_notes (
                id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")

    def get(self, note_id):
        with self.store.connection() as db:
            row = db.execute("SELECT payload FROM voice_notes WHERE id=?", (note_id,)).fetchone()
        if not row:
            raise ServiceError("voice_not_found", "Voice note not found.", 404)
        return json.loads(row[0])

    def list_notes(self):
        with self.store.connection() as db:
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM voice_notes ORDER BY created_at DESC LIMIT 30"
                )
            ]

    def save(self, note):
        assert_safe_serialized(note)
        with self.store.connection(write=True) as db:
            db.execute(
                "UPDATE voice_notes SET payload=? WHERE id=?", (json.dumps(note), note["id"])
            )
        return note

    async def upload(self, content, request_id):
        if not content or len(content) > MAX_AUDIO_BYTES:
            raise ServiceError("invalid_audio_size", "Choose an audio file up to 8 MiB.", 413)
        note_id = str(uuid5(NAMESPACE_URL, "virtual-you-voice:" + request_id))
        digest = hashlib.sha256(content).hexdigest()
        async with self.lock:
            with self.store.connection() as db:
                row = db.execute(
                    "SELECT fingerprint,payload FROM voice_notes WHERE id=?", (note_id,)
                ).fetchone()
            if row:
                if row[0] != digest:
                    raise ServiceError(
                        "request_conflict", "This upload ID belongs to a different recording.", 409
                    )
                return json.loads(row[1])
            result = await asyncio.to_thread(self.transcriber.transcribe, content)
            transcript = redact_text(result["transcript"]).strip()
            if not transcript or len(transcript) > 12000:
                raise ServiceError("invalid_transcript", "Use a shorter, clear voice memo.", 422)
            note = {
                "id": note_id,
                "revision": 1,
                "status": "needs_review",
                "transcript": transcript,
                "created_at": utcnow(),
                "duration_seconds": result["duration_seconds"],
                "language": result["language"],
                "provider": result["provider"],
                "draft_id": None,
                "activity": None,
                "warning": "Review names, numbers, and negations. This is a user-reported memo, not independently verified work.",
            }
            assert_safe_serialized(note)
            with self.store.connection(write=True) as db:
                db.execute(
                    "INSERT INTO voice_notes VALUES(?,?,?,?)",
                    (note_id, digest, json.dumps(note), note["created_at"]),
                )
            return note

    def edit(self, note_id, request):
        with self.store.connection(write=True) as db:
            row = db.execute("SELECT payload FROM voice_notes WHERE id=?", (note_id,)).fetchone()
            if not row:
                raise ServiceError("voice_not_found", "Voice note not found.", 404)
            note = json.loads(row[0])
            self.check_review(note, request.expected_revision)
            note.update(transcript=redact_text(request.transcript), revision=note["revision"] + 1)
            assert_safe_serialized(note)
            db.execute("UPDATE voice_notes SET payload=? WHERE id=?", (json.dumps(note), note_id))
        return note

    @staticmethod
    def check_review(note, revision):
        if note["revision"] != revision or note["status"] != "needs_review":
            raise ServiceError(
                "voice_conflict", "Reopen this memo; it has changed or was already confirmed.", 409
            )

    async def confirm(self, note_id, request):
        request = request.model_copy(deep=True)
        request.project = redact_text(request.project)
        if request.transcript is not None:
            request.transcript = redact_text(request.transcript)
        fingerprint = canonical_hash(request.model_dump(mode="json"))
        async with self.lock:
            note = self.get(note_id)
            if note.get("confirmation") and note["confirmation"] != fingerprint:
                raise ServiceError(
                    "voice_conflict",
                    "This memo was confirmed with different content or destination.",
                    409,
                )
            if note["status"] == "draft_ready":
                return note
            if note["status"] == "needs_review":
                self.check_review(note, request.expected_revision)
                self.store.get_persona(request.recipient_id)
                self.workflow.gateway.validate_destination(request.destination.model_dump())
                if request.transcript is not None:
                    note["transcript"] = redact_text(request.transcript)
                note.update(
                    status="confirmed",
                    confirmation=fingerprint,
                    confirmed_request=request.model_dump(mode="json"),
                    draft_id=str(uuid5(NAMESPACE_URL, "voice-draft:" + note_id)),
                )
                self.save(note)
            if not note["activity"]:
                # The shared normalizer supplies the contract and redaction. A stable ID
                # and timestamp make a restart between file save and DB save idempotent.
                session_id = str(uuid5(NAMESPACE_URL, "voice-activity:" + note_id))

                class Repository(ActivityRecordRepository):
                    def save(self, record):
                        record = ActivityRecord.model_validate(record.model_dump(mode="json"))
                        record.session_id = session_id
                        return super().save(record)

                # Do not overlay this application's Git checkout onto a spoken memo.
                with TemporaryDirectory(prefix="virtual-you-voice-") as empty_workspace:
                    ingest = IngestionService(
                        data_directory=self.settings.data_dir,
                        repository=Repository(self.settings.activity_dir),
                        workspace_root=Path(empty_workspace),
                        now=lambda: datetime.fromisoformat(note["created_at"]),
                    )
                    activity = ingest.ingest_transcript(
                        "voice",
                        "User-reported voice memo (not independently verified): "
                        + note["transcript"],
                    )
                note["activity"] = activity.model_dump(mode="json")
                self.save(note)
            self.retrieval.upsert(note["activity"])
            self.retrieval.assign_project([note["activity"]["session_id"]], request.project)
            try:
                self.store.get_draft(note["draft_id"])
            except ServiceError as error:
                if error.code != "draft_not_found":
                    raise
                await self.workflow.create(
                    DraftRequest(
                        recipient_id=request.recipient_id,
                        destination=request.destination,
                        retrieval={
                            "session_ids": [note["activity"]["session_id"]],
                            "project_ids": [request.project],
                            "sources": ["voice"],
                            "limit": 1,
                        },
                    ),
                    draft_id=note["draft_id"],
                )
            note.update(status="draft_ready", error=None)
            return self.save(note)
