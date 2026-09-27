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


def audio_object_key(language: str, text: str) -> str:
    digest = hashlib.sha256(text.strip().encode()).hexdigest()[:20]
    return f"audio/{language}/{digest}.wav"


def audio_url(language: str, text: str) -> str:
    return f"/api/v1/{audio_object_key(language, text)}"


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
