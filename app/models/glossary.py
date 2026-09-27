"""Per-user glossary: every deck word the user has encountered, and how."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.content import Item


class UserGlossaryEntry(Base):
    __tablename__ = "user_glossary"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    via: Mapped[str] = mapped_column(String(16))  # "card" | "example"

    item: Mapped[Item] = relationship(lazy="joined")
