"""Learner voice recordings (M3 step 1: record and play back).

No speech models are involved. The learner records a take, it lands in S3/R2,
and the UI plays it against the Piper reference clip. Takes accumulate on
purpose — they are the test corpus for recognition and pronunciation scoring
later.

Unlike course audio (app/audio.py) these bytes are user data, so the key is
scoped by user and the stream route is authenticated. Because <audio> and
Audio() never send our bearer token, the client fetches the bytes with the
token and plays them from a blob URL rather than pointing a player at the URL.
"""

import logging
import uuid
from typing import Annotated

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import get_settings
from app.content import _s3_client
from app.db import get_session
from app.pronunciation import (
    PATTERN_MIN_ATTEMPTS,
    SAID,
    compare,
    is_pattern,
    normalise,
    take_spans,
    words,
)
from app.models import Item, ItemExample, PronunciationAttempt, User, UserRecording
from app.speech import (
    SpeechToText,
    TranscriptionError,
    TranscriptionUnavailable,
    WordAligner,
    get_aligner,
    get_transcriber,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/recordings", tags=["recordings"])

MAX_BYTES = 10 * 1024 * 1024  # a spoken sentence is kilobytes; this is slack, not a target

# MediaRecorder output varies by browser: Chrome/Firefox give webm/opus,
# Safari gives mp4/aac. The extension only matters for readability in the
# bucket — playback is driven by the stored content type.
EXTENSIONS = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}


def _base_type(content_type: str) -> str:
    """Drop codec parameters: 'audio/webm;codecs=opus' -> 'audio/webm'."""
    return content_type.split(";")[0].strip().lower()


class RecordingOut(BaseModel):
    id: uuid.UUID
    item_id: int
    example_id: int | None
    content_type: str
    size_bytes: int
    duration_ms: int | None
    created_at: str

    @classmethod
    def of(cls, r: UserRecording) -> "RecordingOut":
        return cls(
            id=r.id,
            item_id=r.item_id,
            example_id=r.example_id,
            content_type=r.content_type,
            size_bytes=r.size_bytes,
            duration_ms=r.duration_ms,
            created_at=r.created_at.isoformat(),
        )


@router.post("", response_model=RecordingOut, status_code=201)
async def create_recording(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    item_id: Annotated[int, Form()],
    audio: Annotated[UploadFile, File()],
    example_id: Annotated[int | None, Form()] = None,
    duration_ms: Annotated[int | None, Form()] = None,
) -> RecordingOut:
    base_type = _base_type(audio.content_type or "")
    if base_type not in EXTENSIONS:
        raise HTTPException(415, f"Unsupported audio type: {audio.content_type!r}")

    data = await audio.read()
    if not data:
        raise HTTPException(400, "Empty recording")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Recording too large")

    if await session.get(Item, item_id) is None:
        raise HTTPException(404, "Unknown item")
    if example_id is not None:
        example = await session.get(ItemExample, example_id)
        if example is None or example.item_id != item_id:
            raise HTTPException(404, "Unknown example for this item")

    recording = UserRecording(
        id=uuid.uuid4(),
        user_id=user.id,
        item_id=item_id,
        example_id=example_id,
        object_key="",  # set below, from the generated id
        content_type=base_type,
        size_bytes=len(data),
        duration_ms=duration_ms,
    )
    recording.object_key = f"recordings/{user.id}/{recording.id}.{EXTENSIONS[base_type]}"

    settings = get_settings()
    try:
        _s3_client().put_object(
            Bucket=settings.s3_bucket,
            Key=recording.object_key,
            Body=data,
            ContentType=base_type,
        )
    except (ClientError, BotoCoreError) as exc:
        # Nearly always the object store being unreachable (MinIO not running
        # in dev). Say so: the learner's microphone is fine and retrying once
        # it is back will work, which "could not store" does not convey.
        logger.exception("Storing recording %s failed", recording.object_key)
        raise HTTPException(502, f"Audio storage unavailable — recording not saved ({type(exc).__name__})")

    session.add(recording)
    await session.commit()
    return RecordingOut.of(recording)


@router.get("", response_model=list[RecordingOut])
async def list_recordings(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    item_id: int,
    example_id: int | None = None,
) -> list[RecordingOut]:
    """Takes for one item, newest first. `example_id` omitted means the headword."""
    query = (
        select(UserRecording)
        .where(
            UserRecording.user_id == user.id,
            UserRecording.item_id == item_id,
            UserRecording.example_id.is_(None)
            if example_id is None
            else UserRecording.example_id == example_id,
        )
        .order_by(UserRecording.created_at.desc())
    )
    return [RecordingOut.of(r) for r in await session.scalars(query)]


async def _owned(session: AsyncSession, user: User, recording_id: uuid.UUID) -> UserRecording:
    recording = await session.get(UserRecording, recording_id)
    # Same 404 whether it is missing or someone else's: never confirm existence.
    if recording is None or recording.user_id != user.id:
        raise HTTPException(404, "Recording not found")
    return recording


@router.get("/{recording_id}/audio")
async def stream_recording(
    recording_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    recording = await _owned(session, user, recording_id)
    try:
        obj = _s3_client().get_object(
            Bucket=get_settings().s3_bucket, Key=recording.object_key
        )
        body = obj["Body"].read()
    except (ClientError, BotoCoreError):
        raise HTTPException(404, "Recording audio missing")
    # Small enough to send whole; the client fetches it as a blob, so there is
    # nothing for byte ranges to buy here.
    return Response(
        content=body,
        media_type=recording.content_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )


class SpanOut(BaseModel):
    start: float  # seconds into the take
    end: float


class WordMarkOut(BaseModel):
    word: str
    verdict: str  # "said" | "slipped" | "missed"
    heard: str | None = None
    #: Where in the take to hear this word back: the word the model found
    #: here (right or wrong), or the stretch between its neighbours where a
    #: missed word should have been. Null when there is nothing to point at,
    #: or the take has not been through the aligner (ADR-043).
    take: SpanOut | None = None


class WordRecordOut(BaseModel):
    """This learner's running record for one word."""

    word: str
    said: int
    attempts: int
    # True once there are enough attempts for the rate to mean something.
    # Below that the client shows the count without drawing a conclusion.
    pattern: bool


class TranscriptOut(BaseModel):
    text: str
    language: str
    # Which model actually ran. ADR-031 requires it to be Norwegian-trained,
    # and a claim is only checkable if it is recorded where the answer is.
    model: str
    duration_ms: int | None = None
    confidence: float | None = None
    # What the learner was asked to say, and how each word of it fared. Never
    # a score: a transcript can lose a word for reasons that have nothing to
    # do with the speaker, which is why `history` exists (ADR-036).
    target: str
    words: list[WordMarkOut]
    history: list[WordRecordOut]
    attempts_for_pattern: int = PATTERN_MIN_ATTEMPTS
    #: Whether the heard words were placed in the take. False means the
    #: aligner was not available, so every `take` is null for that reason
    #: and not because nothing was heard.
    aligned: bool = False


def _audio_bytes(recording: UserRecording) -> bytes | None:
    """The stored take, or None when the object is gone."""
    try:
        obj = _s3_client().get_object(Bucket=get_settings().s3_bucket, Key=recording.object_key)
        return obj["Body"].read()
    except (ClientError, BotoCoreError):
        return None


async def _target_text(session: AsyncSession, recording: UserRecording) -> str:
    if recording.example_id is not None:
        example = await session.get(ItemExample, recording.example_id)
        if example:
            return example.sentence
    item = await session.get(Item, recording.item_id)
    return item.source_text if item else ""


async def _history(
    session: AsyncSession, user: User, language: str, wanted: list[str]
) -> list[WordRecordOut]:
    """Each word's tally for this learner, in the order the sentence uses them."""
    if not wanted:
        return []
    rows = await session.execute(
        select(
            PronunciationAttempt.word,
            func.count().label("attempts"),
            func.count().filter(PronunciationAttempt.said).label("said"),
        )
        .where(
            PronunciationAttempt.user_id == user.id,
            PronunciationAttempt.language_code == language,
            PronunciationAttempt.word.in_(set(wanted)),
        )
        .group_by(PronunciationAttempt.word)
    )
    tally = {w: (said, attempts) for w, attempts, said in rows}
    out = []
    for word in dict.fromkeys(wanted):  # de-duplicated, order preserved
        said, attempts = tally.get(word, (0, 0))
        out.append(
            WordRecordOut(
                word=word, said=said, attempts=attempts, pattern=is_pattern(said, attempts)
            )
        )
    return out


@router.post("/{recording_id}/transcribe", response_model=TranscriptOut)
async def transcribe_recording(
    recording_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    transcriber: Annotated[SpeechToText, Depends(get_transcriber)],
    aligner: Annotated[WordAligner, Depends(get_aligner)],
) -> TranscriptOut:
    """Transcribe a saved take.

    Deliberately against a stored recording rather than a fresh upload: the
    takes from M3 step 1 are the corpus this is meant to be checked on before
    it is trusted, and re-running it on the same take is how it gets compared
    across models.
    """
    recording = await _owned(session, user, recording_id)
    target = await _target_text(session, recording)
    item = await session.get(Item, recording.item_id)
    language = item.language_code if item else "nb"

    # Transcribing the same take twice must not count the attempt twice, so
    # the first transcript is kept and reused.
    text = recording.transcript
    model = recording.transcript_model
    elapsed: int | None = None
    confidence: float | None = None
    first_time = text is None
    audio: bytes | None = None

    if first_time:
        audio = _audio_bytes(recording)
        if audio is None:
            raise HTTPException(404, "Recording audio missing")

        try:
            transcript = await transcriber.transcribe(
                audio, content_type=recording.content_type
            )
        except TranscriptionUnavailable as exc:
            # Not the learner's fault and not a failed attempt: nothing should
            # be scored against them for this.
            logger.warning("transcription unavailable for %s: %s", recording_id, exc)
            raise HTTPException(503, str(exc))
        except TranscriptionError as exc:
            logger.warning("transcription failed for %s: %s", recording_id, exc)
            raise HTTPException(502, str(exc))

        text = transcript.text
        model = transcript.model
        elapsed = transcript.duration_ms
        confidence = transcript.confidence
        recording.transcript = text
        recording.transcript_model = model

    marks = compare(target, text or "")

    # Place the heard words in the take, for playing one of them against the
    # same word of the reference (ADR-043). Once per take, like the
    # transcript, but never on the critical path of the check: an aligner
    # that is down costs the per-word compare and nothing else. Legacy takes
    # get placed the next time they are checked.
    heard_words = words(text or "")
    if recording.transcript_words is None and heard_words:
        if audio is None:
            audio = _audio_bytes(recording)
        if audio is not None:
            try:
                alignment = await aligner.align(
                    audio, content_type=recording.content_type, words=heard_words
                )
            except TranscriptionError as exc:  # TranscriptionUnavailable included
                logger.info("alignment skipped for %s: %s", recording_id, exc)
            else:
                recording.transcript_words = [
                    {"word": w.word, "start": w.start, "end": w.end} if w else None
                    for w in alignment.words
                ]
                recording.alignment_model = alignment.model

    placed = recording.transcript_words
    aligned = placed is not None and len(placed) == len(heard_words)
    spans = (
        take_spans(marks, [(w["start"], w["end"]) if w else None for w in placed])
        if aligned
        else [None] * len(marks)
    )

    if first_time:
        session.add_all(
            PronunciationAttempt(
                user_id=user.id,
                recording_id=recording.id,
                language_code=language,
                word=normalise(m.word),
                # A slip counts against. `klokka` coming back as `klokke` is
                # not a near miss to be forgiven -- it is precisely the
                # distinction the learner is not making, and the most common
                # one in the 2026-10-06 corpus. Only an exact match counts.
                said=m.verdict == SAID,
                heard=(m.heard or None),
            )
            for m in marks
            if normalise(m.word)
        )
    await session.commit()

    return TranscriptOut(
        text=text or "",
        language=language,
        model=model or "unknown",
        duration_ms=elapsed or recording.duration_ms,
        confidence=confidence,
        target=target,
        words=[
            WordMarkOut(
                word=m.word,
                verdict=m.verdict,
                heard=m.heard,
                take=SpanOut(start=span[0], end=span[1]) if span else None,
            )
            for m, span in zip(marks, spans)
        ],
        history=await _history(session, user, language, [normalise(m.word) for m in marks]),
        aligned=aligned,
    )


@router.delete("/{recording_id}", status_code=204)
async def delete_recording(
    recording_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    recording = await _owned(session, user, recording_id)
    try:
        _s3_client().delete_object(Bucket=get_settings().s3_bucket, Key=recording.object_key)
    except (ClientError, BotoCoreError):
        # The row is what the UI lists; a stray object is harmless next to a
        # delete the learner asked for and watched fail.
        pass
    await session.delete(recording)
    await session.commit()
    return Response(status_code=204)
