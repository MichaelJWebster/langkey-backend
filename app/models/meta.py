from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AppMeta(Base):
    """Key-value store for app-level metadata."""

    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(1024))
