"""Talk to a speech service over HTTP.

This is the production shape (ADR-032): the model lives in a sibling
container sized for it, and the API holds only this client. The same class
serves a service running on the M2 Max during development -- only the URL
changes -- which is the point of having the seam.
"""

import logging

import httpx

from app.speech.base import Transcript, TranscriptionError, TranscriptionUnavailable

log = logging.getLogger(__name__)


class HttpTranscriber:
    """POSTs the audio to a /transcribe endpoint and reads the transcript back."""

    name = "http"

    def __init__(self, url: str, *, model: str, timeout_seconds: float = 30.0) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def transcribe(
        self, audio: bytes, *, content_type: str, language: str = "no"
    ) -> Transcript:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.url}/transcribe",
                    files={"audio": ("take", audio, content_type)},
                    data={"language": language},
                )
        except httpx.TimeoutException as exc:
            # A long utterance on a cold container can genuinely take a while;
            # that is unavailability, not a bad recording.
            log.warning("speech service timed out after %ss", self.timeout_seconds)
            raise TranscriptionUnavailable("The speech service did not answer in time") from exc
        except httpx.HTTPError as exc:
            log.warning("speech service unreachable at %s: %s", self.url, exc)
            raise TranscriptionUnavailable("The speech service is unreachable") from exc

        if response.status_code >= 500:
            log.warning("speech service returned %s", response.status_code)
            raise TranscriptionUnavailable(
                f"The speech service returned {response.status_code}"
            )
        if response.status_code >= 400:
            # 4xx means this audio was rejected -- wrong format, too long --
            # which is about the request, not the service's health.
            raise TranscriptionError(
                f"The speech service rejected the audio ({response.status_code})"
            )

        try:
            body = response.json()
            return Transcript(
                text=body["text"],
                language=body.get("language", language),
                model=body.get("model", self.model),
                duration_ms=body.get("duration_ms"),
                confidence=body.get("confidence"),
                segments=tuple(body.get("segments") or ()),
            )
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("speech service sent an unreadable reply: %s", exc)
            raise TranscriptionError("The speech service sent an unreadable reply") from exc
