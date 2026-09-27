"""Per-user learning state: FSRS-scheduled cards and the immutable review log."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.common import utcnow
from app.models.content import Item

# Card direction values for UserCard.direction.
RECOGNITION = "recognition"  # Norwegian -> English
PRODUCTION = "production"  # English -> Norwegian

# fsrs.State.Review: the card has graduated its learning steps.
FSRS_REVIEW_STATE = 2


class UserCard(Base):
    """Per-user FSRS scheduling state for one item in one direction.

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
    due: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    last_review: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reps: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    item: Mapped[Item] = relationship(lazy="joined")

    __table_args__ = (UniqueConstraint("user_id", "item_id", "direction"),)


class ReviewEvent(Base):
    """Immutable log of every review; feeds progress, streaks, and later analytics."""

    __tablename__ = "review_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    user_card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_cards.id"), index=True)
    rating: Mapped[int] = mapped_column(Integer)  # fsrs.Rating value, 1-4
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    was_new: Mapped[bool] = mapped_column(Boolean)  # first-ever review of this card
    due_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stability_after: Mapped[float | None] = mapped_column(Float)
    difficulty_after: Mapped[float | None] = mapped_column(Float)
