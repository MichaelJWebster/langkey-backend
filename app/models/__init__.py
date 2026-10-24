"""ORM models, one module per functional area.

Importing this package registers every table on `app.db.Base.metadata`
(Alembic autogenerate relies on that). Everything is re-exported here, so
callers use `from app.models import ...` without caring about the split.
"""

from app.models.alignments import ClipAlignment
from app.models.content import Item, ItemCategory, ItemExample, Language
from app.models.conversation import Conversation, ConversationTurn, Dialogue
from app.models.exercises import ExerciseAttempt
from app.models.learning import (
    FSRS_REVIEW_STATE,
    PRODUCTION,
    RECOGNITION,
    ReviewEvent,
    UserAnswerAlternative,
    UserCard,
)
from app.models.games import GAMES, GamePair, GameRound, GameSet
from app.models.glossary import UserGlossaryEntry
from app.models.grammar import GrammarAttempt, GrammarExercise, GrammarUnit
from app.models.meta import AppMeta
from app.models.passages import (
    MARKS,
    MARK_CREDIT,
    ROLES,
    Passage,
    PassageAttempt,
    PassageGloss,
    PassageTopic,
)
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
    "ClipAlignment",
    "Conversation",
    "ConversationTurn",
    "Dialogue",
    "ExerciseAttempt",
    "FSRS_REVIEW_STATE",
    "GAMES",
    "GamePair",
    "GameRound",
    "GameSet",
    "GrammarAttempt",
    "GrammarExercise",
    "GrammarUnit",
    "Item",
    "ItemCategory",
    "ItemExample",
    "Language",
    "MARKS",
    "MARK_CREDIT",
    "PRODUCTION",
    "Passage",
    "PassageAttempt",
    "PassageGloss",
    "PassageTopic",
    "RECOGNITION",
    "ROLES",
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
