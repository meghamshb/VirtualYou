"""Adapter for voice text transcribed by an external speech service."""

from typing import Dict


class VoiceTranscriptAdapter:
    """Convert an existing transcript into a source-neutral ingestion event."""

    def adapt(self, text: str) -> Dict[str, str]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("voice transcript must contain nonempty text")
        return {
            "event_type": "transcript",
            "source": "voice",
            "text": text.strip(),
        }

    def from_transcript(self, text: str) -> Dict[str, str]:
        return self.adapt(text)


def voice_transcript_event(text: str) -> Dict[str, str]:
    return VoiceTranscriptAdapter().adapt(text)


VoiceAdapter = VoiceTranscriptAdapter
