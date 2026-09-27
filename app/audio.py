"""Audio storage addressing and streaming.

Clips are content-addressed: the object key is derived from the spoken text,
so regenerating audio after content edits is automatic and idempotent. The
generator (docker/piper) uses the same derivation — keep them in sync.

The audio endpoint is unauthenticated: clips are course content, carry no user
data, and <audio>/Audio() requests don't send our bearer token.
"""

import hashlib

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.config import get_settings
from app.content import _s3_client

router = APIRouter(prefix="/api/v1/audio", tags=["audio"])


# ≈80% speaking speed for learners (Piper length_scale stretches phonemes).
LENGTH_SCALE = 1.25
# Dictation sentences: always the primary (clearest) voice, at a ladder of
# speeds. The server suggests one from recent accuracy; the learner can switch.
DICTATION_SCALES = [2.0, 1.6, 1.25]  # very slow / slow / normal

# Primary voice reads headwords; example sentences draw from the pool so the
# learner hears varied speakers. "nvcc:N" = speaker N of no_NO-nvcc-medium.
PRIMARY_VOICE = "talesyntese"
VOICE_POOL = [PRIMARY_VOICE] + [f"nvcc:{n}" for n in range(10)]


def voice_for_text(text: str, primary: bool = False) -> str:
    if primary:
        return PRIMARY_VOICE
    pick = int(hashlib.sha256(f"voice|{text.strip()}".encode()).hexdigest()[:8], 16)
    return VOICE_POOL[pick % len(VOICE_POOL)]


def audio_object_key(
    language: str, text: str, primary: bool = False, scale: float | None = None
) -> str:
    voice = voice_for_text(text, primary)
    s = scale or LENGTH_SCALE
    digest = hashlib.sha256(f"{voice}|{s}|{text.strip()}".encode()).hexdigest()[:20]
    return f"audio/{language}/{digest}.wav"


def audio_url(
    language: str, text: str, primary: bool = False, scale: float | None = None
) -> str:
    return f"/api/v1/{audio_object_key(language, text, primary, scale)}"


@router.get("/{language}/{filename}")
async def stream_audio(language: str, filename: str) -> StreamingResponse:
    if not filename.endswith(".wav") or "/" in language:
        raise HTTPException(404)
    key = f"audio/{language}/{filename}"
    try:
        obj = _s3_client().get_object(Bucket=get_settings().s3_bucket, Key=key)
    except (ClientError, BotoCoreError):
        raise HTTPException(404, "Audio not generated for this text")
    return StreamingResponse(
        obj["Body"].iter_chunks(),
        media_type="audio/wav",
        # Content-addressed, so safe to cache forever.
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
