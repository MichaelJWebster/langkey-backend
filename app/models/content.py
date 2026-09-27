"""Learnable content: languages and their items (words/phrases in context)."""

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


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
