"""Learner voice recordings: the raw material for M3 speaking work.

Stored as objects in S3/R2 with a row per take. Unlike course audio these are
user data, so the key is scoped by user and the bytes are only ever served
through an authenticated route. Takes are kept rather than overwritten: the
point of M3 step 1 is to accumulate real recordings to test recognition and
pronunciation scoring against later.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import utcnow


class UserRecording(Base):
    """One recorded attempt at saying an item or one of its example sentences."""

    __tablename__ = "user_recordings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    # Null means the headword itself rather than one of its examples.
    example_id: Mapped[int | None] = mapped_column(
        ForeignKey("item_examples.id", ondelete="CASCADE")
    )

    object_key: Mapped[str] = mapped_column(String(255), unique=True)
    content_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    # What the browser reported; MediaRecorder does not always know, hence null.
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    # Filled in the first time the take is transcribed, so asking again is
    # free and never double-counts the attempt (app/routes/recordings.py).
    transcript: Mapped[str | None] = mapped_column(Text)
    transcript_model: Mapped[str | None] = mapped_column(String(128))
    # Where each word of the transcript sits in the take, so one word of it
    # can be played against the same word of the reference (ADR-043). One
    # entry per word of `words(transcript)`: {word, start, end} in seconds,
    # or null where the aligner could not place it. Null as a whole until the
    # take has been through the aligner, which may be later than the
    # transcript -- the check must not wait on it.
    transcript_words: Mapped[list[dict | None] | None] = mapped_column(JSON)
    alignment_model: Mapped[str | None] = mapped_column(String(128))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PronunciationAttempt(Base):
    """One target word, and whether the model found it in one take.

    Append-only, like review_events and exercise_attempts. A single row says
    almost nothing -- a transcript can lose a word for reasons that have
    nothing to do with the speaker -- but the tally across a word's attempts
    is the only honest signal available without forced alignment (ADR-036).
    """

    __tablename__ = "pronunciation_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    recording_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_recordings.id", ondelete="CASCADE"), index=True
    )
    language_code: Mapped[str] = mapped_column(String(8), default="nb")
    # Normalised: lower-case, no punctuation, so attempts tally across
    # sentences that happen to capitalise or punctuate a word differently.
    word: Mapped[str] = mapped_column(String(64), index=True)
    said: Mapped[bool] = mapped_column(Boolean)
    heard: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
