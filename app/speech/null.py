"""The transcriber you get when none is configured.

The default, so a deployment without a speech service behaves predictably:
the route answers 503 and says so, rather than the app failing to start or a
say-it drill silently marking everything wrong.
"""

from app.speech.base import Alignment, Transcript, TranscriptionUnavailable


class NullTranscriber:
    name = "none"

    async def transcribe(
        self, audio: bytes, *, content_type: str, language: str = "no"
    ) -> Transcript:
        raise TranscriptionUnavailable(
            "No speech-to-text backend is configured (LANGKEY_STT_BACKEND)"
        )

    async def align(
        self, audio: bytes, *, content_type: str, words: list[str]
    ) -> Alignment:
        raise TranscriptionUnavailable(
            "No speech-to-text backend is configured (LANGKEY_STT_BACKEND)"
        )
