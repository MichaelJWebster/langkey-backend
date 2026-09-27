"""Exercise attempts: immutable log of non-flashcard practice (M2+)."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import utcnow


class ExerciseAttempt(Base):
    __tablename__ = "exercise_attempts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    exercise_type: Mapped[str] = mapped_column(String(24))  # "dictation" | "listen_choose"
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))
    example_id: Mapped[int | None] = mapped_column(ForeignKey("item_examples.id"))
    correct: Mapped[bool] = mapped_column(Boolean)
    near_miss: Mapped[bool] = mapped_column(Boolean, default=False)  # diacritics only
    hints: Mapped[int] = mapped_column(Integer, default=0)  # words revealed before answering
    speed: Mapped[float | None] = mapped_column(Float)  # dictation length_scale used
    answer: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
