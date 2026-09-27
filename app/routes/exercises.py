import random
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audio import DICTATION_SCALES, audio_url
from app.auth import get_current_user
from app.db import get_session
from app.glossary import _form_index
from app.models import (
    RECOGNITION,
    ExerciseAttempt,
    Item,
    User,
    UserCard,
)

router = APIRouter(prefix="/api/v1/exercises", tags=["exercises"])


class SpeedOut(BaseModel):
    scale: float  # Piper length_scale (bigger = slower)
    url: str


class DictationOut(BaseModel):
    kind: Literal["dictation"] = "dictation"
    item_id: int
    example_id: int
    speeds: list[SpeedOut]  # slowest first
    suggested_scale: float
    target: str  # checked client-side; this is practice, not an exam
    translation: str


class ChoiceOut(BaseModel):
    kind: Literal["listen_choose"] = "listen_choose"
    item_id: int
    audio_url: str
    word: str  # revealed after answering
    options: list[str]  # English translations
    correct_index: int


class ListeningSessionOut(BaseModel):
    exercises: list[DictationOut | ChoiceOut]


class AttemptIn(BaseModel):
    exercise_type: Literal["dictation", "listen_choose"]
    item_id: int | None = None
    example_id: int | None = None
    correct: bool
    near_miss: bool = False
    hints: int = 0
    speed: float | None = None
    answer: str | None = None


def _dictation_share(total_attempts: int) -> float:
    """Word recognition dominates early; transcription grows with practice."""
    if total_attempts < 25:
        return 0.25
    if total_attempts < 75:
        return 0.4
    return 0.6


async def _suggested_scale(session: AsyncSession, user_id) -> float:
    """Pick a dictation speed from recent accuracy (hint-free correct answers)."""
    recent = (
        await session.execute(
            select(ExerciseAttempt.correct, ExerciseAttempt.hints)
            .where(
                ExerciseAttempt.user_id == user_id,
                ExerciseAttempt.exercise_type == "dictation",
            )
            .order_by(ExerciseAttempt.created_at.desc())
            .limit(12)
        )
    ).all()
    if len(recent) < 6:
        return DICTATION_SCALES[0]  # very slow until there's evidence
    accuracy = sum(1 for correct, hints in recent if correct and hints == 0) / len(recent)
    if accuracy < 0.55:
        return DICTATION_SCALES[0]
    if accuracy < 0.8:
        return DICTATION_SCALES[1]
    return DICTATION_SCALES[2]


@router.get("/listening", response_model=ListeningSessionOut)
async def listening_session(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    language: str = "nb",
    limit: int = 8,
) -> ListeningSessionOut:
    seen_items = list(
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
    if not seen_items:
        return ListeningSessionOut(exercises=[])

    total_attempts = (
        await session.scalar(
            select(func.count())
            .select_from(ExerciseAttempt)
            .where(ExerciseAttempt.user_id == user.id)
        )
    ) or 0
    n_dictation = max(1, round(limit * _dictation_share(total_attempts)))
    suggested = await _suggested_scale(session, user.id)

    # Rank candidate sentences by how much of them the learner has already met:
    # matched known deck words over total words (unknown-to-deck words count
    # against), with a mild penalty for length.
    index = await _form_index(session, language)
    seen_ids = {item.id for item in seen_items}
    candidates = []
    for item in seen_items:
        for ex in item.examples:
            words = ex.sentence.split()
            matched = index.match(ex.sentence) | {item.id}
            known = len(matched & seen_ids)
            ease = known / max(len(words), 1) - 0.02 * len(words)
            candidates.append((ease, item, ex))
    candidates.sort(key=lambda c: c[0], reverse=True)
    # Sample among the easiest 60% so sessions vary but stay comprehensible.
    pool = candidates[: max(n_dictation, int(len(candidates) * 0.6))]
    picked = random.sample(pool, k=min(n_dictation, len(pool)))

    dictations = [
        DictationOut(
            item_id=item.id,
            example_id=ex.id,
            speeds=[
                SpeedOut(
                    scale=scale,
                    url=audio_url(language, ex.sentence, primary=True, scale=scale),
                )
                for scale in DICTATION_SCALES
            ],
            suggested_scale=suggested,
            target=ex.sentence,
            translation=ex.translation,
        )
        for _ease, item, ex in picked
    ]

    all_translations = list(
        await session.scalars(
            select(Item.translation).where(Item.language_code == language).distinct()
        )
    )
    choices: list[ChoiceOut] = []
    for item in random.sample(seen_items, k=min(limit - len(dictations), len(seen_items))):
        distractors = random.sample(
            [t for t in all_translations if t != item.translation],
            k=min(3, len(all_translations) - 1),
        )
        options = distractors + [item.translation]
        random.shuffle(options)
        choices.append(
            ChoiceOut(
                item_id=item.id,
                audio_url=audio_url(language, item.source_text, primary=True),
                word=item.source_text,
                options=options,
                correct_index=options.index(item.translation),
            )
        )

    exercises: list[DictationOut | ChoiceOut] = [*dictations, *choices]
    random.shuffle(exercises)
    return ListeningSessionOut(exercises=exercises)


@router.post("/attempts", status_code=201)
async def record_attempt(
    body: AttemptIn,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    session.add(
        ExerciseAttempt(
            user_id=user.id,
            exercise_type=body.exercise_type,
            item_id=body.item_id,
            example_id=body.example_id,
            correct=body.correct,
            near_miss=body.near_miss,
            hints=body.hints,
            speed=body.speed,
            answer=body.answer,
        )
    )
    await session.commit()
    return {"status": "recorded"}
