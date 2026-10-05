"""HTTP front for NB-Whisper. See speech_service/README.md."""

import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from speech_service import transcriber
from speech_service.config import get_speech_settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# A spoken sentence is kilobytes. This is slack for a long answer, not a target.
MAX_BYTES = 25 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_speech_settings()
    if settings.preload:
        # In a scale-to-zero container, loading here means the cold start pays
        # for it once rather than the learner's first utterance waiting.
        transcriber.load(settings)
    yield


app = FastAPI(title="LangKey speech", lifespan=lifespan)


class TranscriptOut(BaseModel):
    text: str
    language: str
    model: str
    duration_ms: int | None = None
    confidence: float | None = None
    segments: list[str] = []


@app.get("/health")
async def health() -> dict:
    settings = get_speech_settings()
    # Reports the configured model without loading it, so a health check does
    # not trigger a multi-gigabyte download.
    return {
        "status": "ok",
        "model": settings.model,
        "device": settings.device,
        "loaded": transcriber.is_loaded(),
        "loaded_model": transcriber.loaded_model(),
    }


@app.post("/transcribe", response_model=TranscriptOut)
async def transcribe(
    audio: Annotated[UploadFile, File()],
    language: Annotated[str, Form()] = "no",
) -> TranscriptOut:
    data = await audio.read()
    if not data:
        raise HTTPException(400, "Empty audio")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, f"Audio larger than {MAX_BYTES // (1024 * 1024)} MB")

    settings = get_speech_settings()
    try:
        result = transcriber.transcribe(data, settings, language=language)
    except Exception as exc:  # noqa: BLE001 - the API maps 5xx to "unavailable"
        # Anything from a corrupt container to a failed model load lands here.
        # The API's HttpTranscriber turns a 5xx into TranscriptionUnavailable,
        # which the learner sees as "not available", never as a bad attempt.
        log.exception("transcription failed")
        raise HTTPException(500, f"Transcription failed: {exc}") from exc
    return TranscriptOut(**result)
