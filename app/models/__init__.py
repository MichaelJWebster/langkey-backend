"""ORM models, one module per functional area.

Importing this package registers every table on `app.db.Base.metadata`
(Alembic autogenerate relies on that). Everything is re-exported here, so
callers use `from app.models import ...` without caring about the split.
"""

from app.models.content import Item, Language
from app.models.learning import (
    FSRS_REVIEW_STATE,
    PRODUCTION,
    RECOGNITION,
    ReviewEvent,
    UserCard,
)
from app.models.meta import AppMeta
from app.models.users import User

__all__ = [
    "AppMeta",
    "FSRS_REVIEW_STATE",
    "Item",
    "Language",
    "PRODUCTION",
    "RECOGNITION",
    "ReviewEvent",
    "User",
    "UserCard",
]
