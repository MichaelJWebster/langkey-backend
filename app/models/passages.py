"""A1 translation passages: short Norwegian texts the learner renders into English.

Distinct from `app.models.reading`, which holds the imported library (novels,
folktales) with a translation already supplied for every sentence. Here the
translation is the exercise: the learner writes theirs, then compares it with
the reference and marks their own work.

Marking is self-assessed on purpose. A free translation has too many right
answers to grade by string comparison, and the alternative -- a model marking
it -- puts a model on the request path for a judgement the learner can make
better themselves.

Passages are authored content, synced from `data/a1_passages_<lang>.jsonl` the
same way the deck is: matched by slug, updated in place so attempt rows keep
pointing at the passage they were about.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

#: What the learner may say about their own attempt. Ordered worst to best;
#: `MARKS.index(mark)` is the score, so the order is load-bearing.
MARKS = ("missed", "partly", "got")


class Passage(Base):
    __tablename__ = "passages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    language_code: Mapped[str] = mapped_column(ForeignKey("languages.code"), index=True)
    level: Mapped[str] = mapped_column(String(8), default="A1")
    kind: Mapped[str] = mapped_column(String(16))  # "sentence" | "paragraph"
    seq: Mapped[int] = mapped_column(Integer)  # authored order, easiest first
    no_text: Mapped[str] = mapped_column(Text)
    en_text: Mapped[str] = mapped_column(Text)  # the reference translation
    word_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    glosses: Mapped[list["PassageGloss"]] = relationship(
        order_by="PassageGloss.position",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    topics: Mapped[list["PassageTopic"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class PassageGloss(Base):
    """One unfamiliar word, in the form it takes in the Norwegian.

    `form` is the inflected surface form as written -- "Hverdagen", not
    "en hverdag" -- because a beginner cannot yet get from one to the other,
    and looking at a dictionary form while reading a definite noun is most of
    what makes glossaries useless. `lemma` is carried alongside for anyone who
    wants it, and equals `form` when the word is already in its base form.
    """

    __tablename__ = "passage_glosses"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    passage_id: Mapped[int] = mapped_column(
        ForeignKey("passages.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    form: Mapped[str] = mapped_column(String(64))
    lemma: Mapped[str] = mapped_column(String(64))
    en: Mapped[str] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("passage_id", "position"),)


class PassageTopic(Base):
    """Which deck categories a passage draws on, for filtering."""

    __tablename__ = "passage_topics"

    passage_id: Mapped[int] = mapped_column(
        ForeignKey("passages.id", ondelete="CASCADE"), primary_key=True
    )
    topic: Mapped[str] = mapped_column(String(32), primary_key=True)


class PassageAttempt(Base):
    """One self-marked attempt. Kept in full: the answers are the record of
    progress, and re-reading an old translation is itself worth something."""

    __tablename__ = "passage_attempts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Nullable so a passage can be retired from the content file without
    # deleting the history of having worked through it.
    passage_id: Mapped[int | None] = mapped_column(
        ForeignKey("passages.id", ondelete="SET NULL"), index=True
    )
    mark: Mapped[str] = mapped_column(String(8))  # one of MARKS
    answer: Mapped[str | None] = mapped_column(Text)
    revealed: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
