"""Talk to a speech service over HTTP.

This is the production shape (ADR-032): the model lives in a sibling
container sized for it, and the API holds only this client. The same class
serves a service running on the M2 Max during development -- only the URL
changes -- which is the point of having the seam.
"""

import json
import logging

import httpx

from app.speech.base import (
    Alignment,
    CandidateScore,
    CandidateScores,
    Transcript,
    TranscriptionError,
    TranscriptionUnavailable,
    WordSpan,
)

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
        response = await self._post(
            "/transcribe", audio, content_type=content_type, data={"language": language}
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

    async def align(
        self, audio: bytes, *, content_type: str, words: list[str]
    ) -> Alignment:
        response = await self._post(
            "/align", audio, content_type=content_type, data={"words": json.dumps(words)}
        )
        try:
            body = response.json()
            spans = body["words"]
            if len(spans) != len(words):
                raise ValueError(f"{len(spans)} spans for {len(words)} words")
            return Alignment(
                words=tuple(
                    WordSpan(word=w, start=float(s["start"]), end=float(s["end"]))
                    if s is not None
                    else None
                    for w, s in zip(words, spans)
                ),
                model=body.get("model", "unknown"),
            )
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("speech service sent an unreadable alignment: %s", exc)
            raise TranscriptionError("The speech service sent an unreadable reply") from exc

    async def score(
        self, audio: bytes, *, content_type: str, candidates: list[str]
    ) -> CandidateScores:
        response = await self._post(
            "/score", audio, content_type=content_type,
            data={"candidates": json.dumps(candidates, ensure_ascii=False)},
        )
        try:
            body = response.json()
            rows = body["candidates"]
            if len(rows) != len(candidates):
                raise ValueError(f"{len(rows)} scores for {len(candidates)} candidates")
            return CandidateScores(
                candidates=tuple(
                    CandidateScore(
                        text=text,
                        ctc=None if r["ctc"] is None else float(r["ctc"]),
                        letters=int(r["letters"]),
                    )
                    for text, r in zip(candidates, rows)
                ),
                model=body.get("model", "unknown"),
            )
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("speech service sent unreadable scores: %s", exc)
            raise TranscriptionError("The speech service sent an unreadable reply") from exc

    async def _post(
        self, path: str, audio: bytes, *, content_type: str, data: dict[str, str]
    ) -> httpx.Response:
        """One request to the service, with failures sorted into the two
        kinds the routes answer differently."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.url}{path}",
                    files={"audio": ("take", audio, content_type)},
                    data=data,
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
        if response.status_code in (404, 405):
            # The service does not offer this endpoint at all -- an older
            # build that transcribes but cannot align. That is the feature
            # being unavailable, not this audio being rejected, and the
            # routes answer it as such (503, nothing counted against anyone).
            raise TranscriptionUnavailable(
                f"The speech service does not offer {path} ({response.status_code})"
            )
        if response.status_code >= 400:
            # 4xx means this audio was rejected -- wrong format, too long --
            # which is about the request, not the service's health.
            raise TranscriptionError(
                f"The speech service rejected the audio ({response.status_code})"
            )
        return response
