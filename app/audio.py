"""Audio storage addressing and streaming.

Clips are content-addressed: the object key is derived from the spoken text,
so regenerating audio after content edits is automatic and idempotent. The
generator (docker/piper) uses the same derivation — keep them in sync.

The audio endpoint is unauthenticated: clips are course content, carry no user
data, and <audio>/Audio() requests don't send our bearer token.
"""

import hashlib
import re

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.config import get_settings
from app.content import _s3_client

router = APIRouter(prefix="/api/v1/audio", tags=["audio"])


# ≈80% speaking speed for learners. Piper's length_scale is severely
# sub-linear on these voices (measured: 1.25→1.06x, 1.6→1.28x, 2.8→1.9x
# actual voiced slowdown), so these values are calibrated by ear/measurement,
# not nominal ratios.
LENGTH_SCALE = 1.6
# Dictation sentences: always the primary (clearest) voice, at a ladder of
# speeds. The server suggests one from recent accuracy; the learner can switch.
DICTATION_SCALES = [2.8, 2.2, 1.6]  # very slow / slow / normal

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


# A single `bytes=a-b` range, either end optional ("bytes=0-", "bytes=-500").
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _parse_range(header: str | None) -> str | None:
    """Normalise a single byte range, or None to serve the whole object.

    Multi-range and malformed headers return None: a server may always ignore
    Range and reply 200, and media elements never ask for multiple ranges.
    """
    if not header:
        return None
    match = _RANGE_RE.match(header.strip())
    if not match:
        return None
    start, end = match.group(1), match.group(2)
    if not start and not end:
        return None
    return f"bytes={start}-{end}"


@router.get("/{language}/{filename}")
async def stream_audio(language: str, filename: str, request: Request) -> StreamingResponse:
    """Stream a clip, honouring byte ranges.

    Safari and iOS refuse to play an <audio> source that does not advertise
    Accept-Ranges and answer 206, so the range is forwarded to S3/R2 and its
    reply mirrored rather than being served by slicing here.
    """
    if not filename.endswith(".wav") or "/" in language:
        raise HTTPException(404)
    key = f"audio/{language}/{filename}"
    bucket = get_settings().s3_bucket
    wanted = _parse_range(request.headers.get("range"))

    client = _s3_client()
    try:
        obj = client.get_object(**{"Bucket": bucket, "Key": key, **({"Range": wanted} if wanted else {})})
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("InvalidRange", "416"):
            # 416 must carry the real length so the client can ask again.
            try:
                total = client.head_object(Bucket=bucket, Key=key)["ContentLength"]
            except (ClientError, BotoCoreError):
                total = "*"
            raise HTTPException(
                416,
                "Requested range not satisfiable",
                headers={"Content-Range": f"bytes */{total}", "Accept-Ranges": "bytes"},
            )
        raise HTTPException(404, "Audio not generated for this text")
    except BotoCoreError:
        raise HTTPException(404, "Audio not generated for this text")

    headers = {
        # Content-addressed, so safe to cache forever.
        "Cache-Control": "public, max-age=31536000, immutable",
        "Accept-Ranges": "bytes",
        "Content-Length": str(obj["ContentLength"]),
    }
    status = 200
    if obj.get("ContentRange"):
        headers["Content-Range"] = obj["ContentRange"]
        status = 206
    return StreamingResponse(
        obj["Body"].iter_chunks(),
        status_code=status,
        media_type="audio/wav",
        headers=headers,
    )
