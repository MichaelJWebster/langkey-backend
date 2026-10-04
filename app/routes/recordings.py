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

import uuid
from typing import Annotated

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import get_settings
from app.content import _s3_client
from app.db import get_session
from app.models import Item, ItemExample, User, UserRecording

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
    except (ClientError, BotoCoreError):
        raise HTTPException(502, "Could not store the recording")

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
