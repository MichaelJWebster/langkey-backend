"""The contract between the app and whatever transcribes Norwegian speech."""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class TranscriptionError(Exception):
    """The audio reached a model and no transcript came back."""


class TranscriptionUnavailable(TranscriptionError):
    """No transcriber is configured, or the one configured cannot be reached.

    Separate from TranscriptionError because the route answers it differently:
    this is a 503 the learner should see as "not available right now", not a
    failed attempt that counts against them.
    """


@dataclass(frozen=True)
class Transcript:
    """What a model heard.

    `text` is the only field a caller needs. The rest exists because a
    say-it drill has to decide whether a poor match is a mispronunciation or
    a model that was guessing (M3 step 3), and because ADR-031 requires the
    model to be Norwegian-trained -- `model` records which one actually ran,
    so that claim stays checkable in the data rather than only in a document.
    """

    text: str
    language: str
    model: str
    duration_ms: int | None = None
    # Mean log-probability per segment where the backend reports it. Whisper
    # derivatives do; a hosted API may not, hence optional.
    confidence: float | None = None
    segments: tuple[str, ...] = field(default_factory=tuple)


@runtime_checkable
class SpeechToText(Protocol):
    """One method, because that is all a say-it drill needs.

    Implementations take the audio bytes as recorded by the browser --
    webm/opus on Chrome and Firefox, mp4 on Safari -- and are responsible for
    whatever decoding that needs. Callers must not have to care.
    """

    name: str

    async def transcribe(
        self, audio: bytes, *, content_type: str, language: str = "no"
    ) -> Transcript: ...
