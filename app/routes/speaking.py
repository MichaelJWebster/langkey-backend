"""Speaking practice: say the phrase, hear how it landed.

Separate from the writing cards on purpose. Recording lived inside the review
panel because that is where the sentences were, but a learner reaching for
pronunciation practice should not have to grade a translation first, and a
learner writing translations should not be interrupted by a microphone
(ADR-037).

Phrases are drawn from what the learner has already met, and weighted toward
the words their own record says are not landing -- which is what the
`pronunciation_attempts` tally exists for (ADR-036).
"""

import random
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audio import PRONUNCIATION_SCALE, PRONUNCIATION_SCALES, audio_url
from app.auth import get_current_user
from app.db import get_session
from app.models import Item, PronunciationAttempt, User, UserCard
from app.models.learning import RECOGNITION
from app.pronunciation import PATTERN_MIN_ATTEMPTS, is_pattern, normalise
from app.sampling import weighted_sample

router = APIRouter(prefix="/api/v1/speaking", tags=["speaking"])

# How much a phrase is favoured for containing a word that is not landing.
# Enough to bring it round sooner, not so much that a session becomes the same
# three sentences.
NEED_WEIGHT = 3.0

# Phrases in one practice session. Michael, on the sessions of 12: *"The
# speaking pronunciation sections are very long. Could we just include 6-8
# questions in them, instead of the 12 we have now?"* Speaking costs more per
# item than any other exercise -- record, wait for the check, listen back --
# so a dozen is a long sit, and a session he puts off is worth less than a
# shorter one he finishes. The top of the range he gave, because the
# weighting towards sounds that are not landing needs a few slots to show up
# in at all.
SESSION_PHRASES = 8


class SpeedOut(BaseModel):
    scale: float
    url: str


class PhraseOut(BaseModel):
    item_id: int
    example_id: int | None  # null means the headword itself
    text: str
    translation: str
    audio_url: str  # the default rung, for a client that offers no chooser
    speeds: list[SpeedOut]
    # Words in this phrase the learner's record says are not landing. The
    # reason this phrase was chosen, shown so the choice is not mysterious.
    focus: list[str] = []


class WordRecordOut(BaseModel):
    word: str
    said: int
    attempts: int
    pattern: bool


class SpeakingSessionOut(BaseModel):
    phrases: list[PhraseOut]
    # The whole record, worst first -- the overview above the session.
    record: list[WordRecordOut]
    attempts_for_pattern: int = PATTERN_MIN_ATTEMPTS


def _speeds(language: str, text: str) -> list[SpeedOut]:
    return [
        SpeedOut(scale=s, url=audio_url(language, text, primary=True, scale=s))
        for s in PRONUNCIATION_SCALES
    ]


async def _word_record(
    session: AsyncSession, user: User, language: str
) -> list[WordRecordOut]:
    rows = await session.execute(
        select(
            PronunciationAttempt.word,
            func.count().label("attempts"),
            func.count().filter(PronunciationAttempt.said).label("said"),
        )
        .where(
            PronunciationAttempt.user_id == user.id,
            PronunciationAttempt.language_code == language,
        )
        .group_by(PronunciationAttempt.word)
    )
    record = [
        WordRecordOut(word=w, said=said, attempts=attempts, pattern=is_pattern(said, attempts))
        for w, attempts, said in rows
    ]
    # Worst first, and among equals the one with more evidence behind it.
    record.sort(key=lambda r: (r.said / r.attempts, -r.attempts))
    return record


@router.get("/practice", response_model=SpeakingSessionOut)
async def practice(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    language: str = "nb",
    limit: int = SESSION_PHRASES,
) -> SpeakingSessionOut:
    """A set of phrases to say, favouring the sounds that are not landing."""
    seen = list(
        await session.scalars(
            select(Item)
            .join(UserCard, UserCard.item_id == Item.id)
            .where(
                UserCard.user_id == user.id,
                UserCard.direction == RECOGNITION,
                UserCard.last_review.is_not(None),
                Item.language_code == language,
            )
        )
    )
    record = await _word_record(session, user, language)
    if not seen:
        return SpeakingSessionOut(phrases=[], record=record)

    struggling = {r.word for r in record if r.pattern}

    candidates: list[tuple[Item, object | None, str, str]] = []
    for item in seen:
        candidates.append((item, None, item.source_text, item.translation))
        for ex in item.examples:
            candidates.append((item, ex, ex.sentence, ex.translation))

    def focus_words(text: str) -> list[str]:
        return [w for w in dict.fromkeys(normalise(text).split()) if w in struggling]

    weights = [1.0 + NEED_WEIGHT * bool(focus_words(text)) for _i, _e, text, _t in candidates]
    picked = weighted_sample(weights, limit)
    random.shuffle(picked)

    phrases = []
    for i in picked:
        item, example, text, translation = candidates[i]
        phrases.append(
            PhraseOut(
                item_id=item.id,
                example_id=getattr(example, "id", None),
                text=text,
                translation=translation,
                audio_url=audio_url(language, text, primary=True, scale=PRONUNCIATION_SCALE),
                speeds=_speeds(language, text),
                focus=focus_words(text),
            )
        )
    return SpeakingSessionOut(phrases=phrases, record=record)
