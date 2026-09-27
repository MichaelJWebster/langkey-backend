"""ORM models, one module per functional area.

Importing this package registers every table on `app.db.Base.metadata`
(Alembic autogenerate relies on that). Everything is re-exported here, so
callers use `from app.models import ...` without caring about the split.
"""

from app.models.content import Item, ItemExample, Language
from app.models.exercises import ExerciseAttempt
from app.models.learning import (
    FSRS_REVIEW_STATE,
    PRODUCTION,
    RECOGNITION,
    ReviewEvent,
    UserCard,
)
from app.models.glossary import UserGlossaryEntry
from app.models.meta import AppMeta
from app.models.users import User

__all__ = [
    "AppMeta",
    "ExerciseAttempt",
    "FSRS_REVIEW_STATE",
    "Item",
    "ItemExample",
    "Language",
    "PRODUCTION",
    "RECOGNITION",
    "ReviewEvent",
    "User",
    "UserCard",
    "UserGlossaryEntry",
]
