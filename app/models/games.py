"""Games: gamified practice built on the vocabulary, kept apart from the FSRS loop.

A game set is authored content, synced from `data/games_<lang>.jsonl` the same
way the deck and the passages are. A round is one completed play, recorded so a
set can show a best result -- the only progress a game keeps. Nothing here
feeds the scheduler or the CEFR estimate: matching ten words against the clock
is practice and fun, not evidence of recall at an interval, and pretending
otherwise would corrupt numbers that mean something.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

#: The kinds of game a set can be. One for now; the subsection in the UI is
#: named after this, so adding a kind adds a subsection.
GAMES = ("matching",)


class GameSet(Base):
    __tablename__ = "game_sets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    language_code: Mapped[str] = mapped_column(ForeignKey("languages.code"), index=True)
    game: Mapped[str] = mapped_column(String(24))  # one of GAMES
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text)
    seq: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    pairs: Mapped[list["GamePair"]] = relationship(
        order_by="GamePair.position", cascade="all, delete-orphan", lazy="selectin"
    )


class GamePair(Base):
    """One English word and its Norwegian equivalent.

    `note` disambiguates where the English alone would not: "how long" is
    `hvor lenge` for time and `hvor langt` for distance, and a matching game
    with two cards reading "how long" is unplayable rather than hard.
    """

    __tablename__ = "game_pairs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    set_id: Mapped[int] = mapped_column(
        ForeignKey("game_sets.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    en: Mapped[str] = mapped_column(String(64))
    no: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (UniqueConstraint("set_id", "position"),)


class GameRound(Base):
    """One completed play. Incomplete rounds are not recorded: a set abandoned
    halfway is not a result, and counting it would make the best score a
    function of how often he gave up."""

    __tablename__ = "game_rounds"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Nullable so a set can be retired from the content file without deleting
    # the record of having played it.
    set_id: Mapped[int | None] = mapped_column(
        ForeignKey("game_sets.id", ondelete="SET NULL"), index=True
    )
    pairs: Mapped[int] = mapped_column(Integer)  # how many were in the set
    mistakes: Mapped[int] = mapped_column(Integer)
    seconds: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
