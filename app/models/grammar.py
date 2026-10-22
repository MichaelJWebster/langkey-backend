"""Grammar practice: authored exercises grouped by rule, and the learner's answers.

A topic (V2 word order, Negation, ...) holds units, one per rule or variation
of a rule ("Time first", "Two verbs"). A unit holds exercises. The rule
diagrams themselves are presentation and live in the frontend, keyed by
topic and unit slug; what is stored here is what gets marked.

Answers are checked in the browser, the same way dictation is: the exercise
carries its accepted answers, and the client posts whether the learner's
matched. A grammar answer is near-unique, unlike a free translation, so a
string comparison is a fair judge and self-marking would only add a step.

Synced from `data/grammar_<lang>.json` by slug, like the passages: rows are
updated in place so attempt history keeps pointing at the exercise it was for.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class GrammarUnit(Base):
    """One rule, or one variation of a rule, inside a topic.

    The topic's own title and blurb ride along on every unit row rather than
    in a table of their own: there are six topics, nothing references one, and
    a second table would buy a join and a second sync for no gain.
    """

    __tablename__ = "grammar_units"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    language_code: Mapped[str] = mapped_column(ForeignKey("languages.code"), index=True)
    topic: Mapped[str] = mapped_column(String(32))
    unit: Mapped[str] = mapped_column(String(32))
    topic_title: Mapped[str] = mapped_column(String(128))
    topic_blurb: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(128))
    blurb: Mapped[str] = mapped_column(Text)
    #: A review unit has no exercises of its own and draws on every other unit
    #: in its topic.
    review: Mapped[bool] = mapped_column(Boolean, default=False)
    seq: Mapped[int] = mapped_column(Integer)

    __table_args__ = (UniqueConstraint("language_code", "topic", "unit"),)


class GrammarExercise(Base):
    __tablename__ = "grammar_exercises"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    unit_id: Mapped[int] = mapped_column(
        ForeignKey("grammar_units.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))  # one of GRAMMAR_KINDS
    #: Everything the UI needs to draw and mark it: instr, prompt, cue, hint,
    #: tiles, options, answers, why, ref. JSON because the shape varies by kind
    #: and nothing queries inside it.
    data: Mapped[dict] = mapped_column(JSON)


class GrammarAttempt(Base):
    """One answered exercise. Immutable, like every other attempt log."""

    __tablename__ = "grammar_attempts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Nullable so an exercise can be retired from the content file without
    # deleting the history of having answered it.
    exercise_id: Mapped[int | None] = mapped_column(
        ForeignKey("grammar_exercises.id", ondelete="SET NULL"), index=True
    )
    correct: Mapped[bool] = mapped_column(Boolean)
    answer: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
