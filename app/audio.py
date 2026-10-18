"""Audio storage addressing and streaming.

Clips are content-addressed: the object key is derived from the spoken text,
so regenerating audio after content edits is automatic and idempotent. The
generator (docker/piper) uses the same derivation — keep them in sync.

The audio endpoint is unauthenticated: clips are course content, carry no user
data, and <audio>/Audio() requests don't send our bearer token.
"""

import hashlib
import logging
import re

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.config import get_settings
from app.content import _s3_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/audio", tags=["audio"])

# S3 error codes meaning "the object is genuinely not there", as opposed to
# "the store could not be reached". The difference matters to the client: a
# missing clip is a content gap, an unreachable store is an outage, and
# collapsing both into 404 is what let a dead MinIO go unnoticed for a session.
MISSING_CODES = {"NoSuchKey", "NoSuchBucket", "404", "NotFound"}


# ≈80% speaking speed for learners. Piper's length_scale is severely
# sub-linear on these voices (measured: 1.25→1.06x, 1.6→1.28x, 2.8→1.9x
# actual voiced slowdown), so these values are calibrated by ear/measurement,
# not nominal ratios.
LENGTH_SCALE = 1.6
# Dictation sentences: always the primary (clearest) voice, at a ladder of
# speeds. The server suggests one from recent accuracy; the learner can switch.
# Defined below as the pronunciation ladder itself (ADR-046).

# Imitation targets are a different job from listening practice, and need
# their own voice and speed (ADR-033). To copy a sound you have to hear it
# separately first, which the flashcard pace does not allow. Rates measured
# over seven sentences, voiced seconds only (20 ms frames above -45 dBFS, so
# trailing silence cannot flatter a slow clip):
#
#   pool voices @1.6   5.26 syl/s   <- what the review panel used to play
#   primary     @1.6   4.66 syl/s
#   primary     @2.2   3.77 syl/s
#   primary     @2.8   3.02 syl/s
#   primary     @3.4   2.63 syl/s   <- half the pace of the old clip
#   primary     @4.0   2.19 syl/s
#   the learner        3.96 syl/s
#
# 3.4 is the learner's own call: "too fast, and the voices vary too much for
# me to learn correct pronunciation ... slow down at least 50%". It is the
# first rung at or past half the old rate, and it is the default. The whole
# ladder is offered, because the right pace is a judgement the learner makes
# per sentence, not a constant: a word they have met a hundred times does not
# need 2.2 seconds of vowel.
#
# Deliberately *not* the dictation ladder, which shares three of its values by
# coincidence rather than by design. Dictation is comprehension practice and
# wants a pace you have to keep up with; these two ladders must be free to
# move apart.
PRONUNCIATION_SCALES = [4.0, 3.4, 2.8, 2.2, 1.6]  # slowest first, as the UI shows them
PRONUNCIATION_SCALE = 3.4

# Dictation offers the same five speeds (ADR-046). Michael: "For the listening
# practice we only have 3 speeds. I'd like the same speeds as for the speaking
# section - ie. 5 speeds." The note above about the ladders being free to move
# apart was a designer's hedge; the learner wants one ladder, so it is one
# list. Both use the primary voice, so every dictation clip at every speed is
# the same file the pronunciation ladder already generated -- no new audio.
DICTATION_SCALES = PRONUNCIATION_SCALES
# The rungs the server suggests from recent accuracy: struggling, middling,
# comfortable. Unchanged from the three-speed ladder, so adding the two slower
# speeds does not slow anyone down unasked; those are the learner's to pick.
DICTATION_SUGGESTED = (2.8, 2.2, 1.6)

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
        code = exc.response.get("Error", {}).get("Code")
        if code in ("InvalidRange", "416"):
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
        if code in MISSING_CODES:
            logger.warning("Audio clip not generated: %s", key)
            raise HTTPException(404, "Audio not generated for this text")
        logger.error("Audio store rejected %s: %s", key, code)
        raise HTTPException(503, "Audio storage unavailable")
    except BotoCoreError as exc:
        # Connection, timeout or credential failure: the store is unreachable.
        logger.error("Audio store unreachable for %s: %s", key, type(exc).__name__)
        raise HTTPException(503, "Audio storage unavailable")

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
