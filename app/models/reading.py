"""Reading content: full texts stored sentence-by-sentence with translations.

Texts are imported by scripts/import_text.py from staging files produced by
scripts/prepare_text.py (fetch -> segment -> translate). The Norwegian
sentences are immutable after import; translations are versioned rows so a
draft (e.g. gemma3) can later be replaced by a better one without touching
the text itself.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ReadingText(Base):
    __tablename__ = "texts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(128), unique=True)
    title: Mapped[str] = mapped_column(String(256))
    author: Mapped[str | None] = mapped_column(String(256))
    language_code: Mapped[str] = mapped_column(ForeignKey("languages.code"))
    kind: Mapped[str] = mapped_column(String(24))  # "folktale" | "novel" | ...
    orthography: Mapped[str] = mapped_column(String(24), default="nb")
    source_url: Mapped[str | None] = mapped_column(String(512))
    license: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    sections: Mapped[list["TextSection"]] = relationship(
        order_by="TextSection.seq", cascade="all, delete-orphan"
    )


class TextSection(Base):
    """A chapter or part; single-section texts have one untitled section."""

    __tablename__ = "text_sections"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    text_id: Mapped[int] = mapped_column(
        ForeignKey("texts.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(String(256))

    __table_args__ = (UniqueConstraint("text_id", "seq"),)


class Sentence(Base):
    """One Norwegian sentence, in reading order. Immutable after import."""

    __tablename__ = "sentences"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    text_id: Mapped[int] = mapped_column(ForeignKey("texts.id", ondelete="CASCADE"))
    section_id: Mapped[int | None] = mapped_column(
        ForeignKey("text_sections.id", ondelete="CASCADE")
    )
    seq: Mapped[int] = mapped_column(Integer)  # order within the text
    paragraph: Mapped[int] = mapped_column(Integer)  # for rendering breaks
    no_text: Mapped[str] = mapped_column(Text)
    audio_key: Mapped[str | None] = mapped_column(String(256))  # future Piper clips

    translations: Mapped[list["SentenceTranslation"]] = relationship(
        cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("text_id", "seq"),)


class SentenceTranslation(Base):
    __tablename__ = "sentence_translations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sentence_id: Mapped[int] = mapped_column(
        ForeignKey("sentences.id", ondelete="CASCADE"), index=True
    )
    translator: Mapped[str] = mapped_column(String(64))  # "gemma3:12b" | "claude" | ...
    en_text: Mapped[str] = mapped_column(Text)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        # exactly one active translation per sentence
        Index(
            "one_current_translation",
            "sentence_id",
            unique=True,
            postgresql_where=is_current.is_(True),
            sqlite_where=is_current.is_(True),
        ),
    )


class UserReadingPosition(Base):
    """Resume point per user per text; doubles as the pagination cursor."""

    __tablename__ = "user_reading_positions"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    text_id: Mapped[int] = mapped_column(
        ForeignKey("texts.id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
