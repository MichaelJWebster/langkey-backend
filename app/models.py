import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


# Card direction values for UserCard.direction.
RECOGNITION = "recognition"  # Norwegian -> English
PRODUCTION = "production"  # English -> Norwegian

# fsrs.State.Review: the card has graduated its learning steps.
FSRS_REVIEW_STATE = 2


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AppMeta(Base):
    """Key-value store for app-level metadata."""

    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(1024))


class Language(Base):
    __tablename__ = "languages"

    code: Mapped[str] = mapped_column(String(8), primary_key=True)  # BCP 47, e.g. "nb"
    name: Mapped[str] = mapped_column(String(64))


class Item(Base):
    """A learnable unit of content: a word or phrase with context."""

    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    language_code: Mapped[str] = mapped_column(ForeignKey("languages.code"), index=True)
    item_type: Mapped[str] = mapped_column(String(16))  # "word" | "phrase"
    source_text: Mapped[str] = mapped_column(String(200))  # the Norwegian
    translation: Mapped[str] = mapped_column(String(200))  # the English
    pos: Mapped[str | None] = mapped_column(String(24))  # part of speech
    cefr_level: Mapped[str] = mapped_column(String(4))  # "A1" | "A2" | ...
    frequency_rank: Mapped[int] = mapped_column(Integer, index=True)  # introduction order
    example_sentence: Mapped[str] = mapped_column(Text)
    example_translation: Mapped[str] = mapped_column(Text)
    audio_key: Mapped[str | None] = mapped_column(String(256))  # MinIO key, from M2

    __table_args__ = (UniqueConstraint("language_code", "source_text", "translation"),)


class UserCard(Base):
    """Per-user FSRS scheduling state for one item in one direction.

    direction: "recognition" (NO -> EN) or "production" (EN -> NO).
    FSRS columns mirror py-fsrs Card; a card with last_review NULL is unseen.
    """

    __tablename__ = "user_cards"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    direction: Mapped[str] = mapped_column(String(16))
    fsrs_state: Mapped[int] = mapped_column(Integer, default=1)  # fsrs.State value
    fsrs_step: Mapped[int | None] = mapped_column(Integer, default=0)
    stability: Mapped[float | None] = mapped_column(Float)
    difficulty: Mapped[float | None] = mapped_column(Float)
    due: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    last_review: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reps: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    item: Mapped[Item] = relationship(lazy="joined")

    __table_args__ = (UniqueConstraint("user_id", "item_id", "direction"),)


class ReviewEvent(Base):
    """Immutable log of every review; feeds progress, streaks, and later analytics."""

    __tablename__ = "review_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    user_card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_cards.id"), index=True)
    rating: Mapped[int] = mapped_column(Integer)  # fsrs.Rating value, 1-4
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    was_new: Mapped[bool] = mapped_column(Boolean)  # first-ever review of this card
    due_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stability_after: Mapped[float | None] = mapped_column(Float)
    difficulty_after: Mapped[float | None] = mapped_column(Float)
