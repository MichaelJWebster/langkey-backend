"""ORM models, one module per functional area.

Importing this package registers every table on `app.db.Base.metadata`
(Alembic autogenerate relies on that). Everything is re-exported here, so
callers use `from app.models import ...` without caring about the split.
"""

from app.models.content import Item, ItemCategory, ItemExample, Language
from app.models.exercises import ExerciseAttempt
from app.models.learning import (
    FSRS_REVIEW_STATE,
    PRODUCTION,
    RECOGNITION,
    ReviewEvent,
    UserAnswerAlternative,
    UserCard,
)
from app.models.glossary import UserGlossaryEntry
from app.models.meta import AppMeta
from app.models.recordings import PronunciationAttempt, UserRecording
from app.models.reading import (
    ReadingText,
    Sentence,
    SentenceTranslation,
    TextSection,
    UserReadingPosition,
)
from app.models.users import User

__all__ = [
    "AppMeta",
    "ExerciseAttempt",
    "FSRS_REVIEW_STATE",
    "Item",
    "ItemCategory",
    "ItemExample",
    "Language",
    "PRODUCTION",
    "RECOGNITION",
    "ReadingText",
    "ReviewEvent",
    "Sentence",
    "SentenceTranslation",
    "TextSection",
    "User",
    "UserAnswerAlternative",
    "UserCard",
    "UserGlossaryEntry",
    "UserReadingPosition",
    "PronunciationAttempt",
    "UserRecording",
]
