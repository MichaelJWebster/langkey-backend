"""Learnable content: languages, items (words/phrases), and their examples."""

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Language(Base):
    __tablename__ = "languages"

    code: Mapped[str] = mapped_column(String(8), primary_key=True)  # BCP 47, e.g. "nb"
    name: Mapped[str] = mapped_column(String(64))


class Item(Base):
    """A learnable unit of content: a word or phrase."""

    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    language_code: Mapped[str] = mapped_column(ForeignKey("languages.code"), index=True)
    item_type: Mapped[str] = mapped_column(String(16))  # "word" | "phrase"
    source_text: Mapped[str] = mapped_column(String(200))  # the Norwegian
    translation: Mapped[str] = mapped_column(String(200))  # the English
    pos: Mapped[str | None] = mapped_column(String(24))  # part of speech
    cefr_level: Mapped[str] = mapped_column(String(4))  # "A1" | "A2" | ...
    frequency_rank: Mapped[int] = mapped_column(Integer, index=True)  # introduction order
    audio_key: Mapped[str | None] = mapped_column(String(256))  # MinIO key, from M2

    examples: Mapped[list["ItemExample"]] = relationship(
        order_by="ItemExample.position",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (UniqueConstraint("language_code", "source_text", "translation"),)


class ItemExample(Base):
    """One example sentence for an item; items carry several, varied contexts."""

    __tablename__ = "item_examples"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    sentence: Mapped[str] = mapped_column(Text)  # the Norwegian
    translation: Mapped[str] = mapped_column(Text)  # the English
    audio_key: Mapped[str | None] = mapped_column(String(256))  # MinIO key, from M2
