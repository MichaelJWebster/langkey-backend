import random
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audio import audio_url
from app.auth import get_current_user
from app.db import get_session
from app.models import (
    RECOGNITION,
    ExerciseAttempt,
    Item,
    ItemExample,
    User,
    UserCard,
)

router = APIRouter(prefix="/api/v1/exercises", tags=["exercises"])


class DictationOut(BaseModel):
    kind: Literal["dictation"] = "dictation"
    item_id: int
    example_id: int
    audio_url: str
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
    answer: str | None = None


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

    all_translations = list(
        await session.scalars(
            select(Item.translation).where(Item.language_code == language).distinct()
        )
    )

    n_dictation = (limit + 1) // 2
    examples = random.sample(
        [(item, ex) for item in seen_items for ex in item.examples],
        k=min(n_dictation, sum(len(i.examples) for i in seen_items)),
    )
    dictations = [
        DictationOut(
            item_id=item.id,
            example_id=ex.id,
            audio_url=audio_url(language, ex.sentence),
            target=ex.sentence,
            translation=ex.translation,
        )
        for item, ex in examples
    ]

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
            answer=body.answer,
        )
    )
    await session.commit()
    return {"status": "recorded"}
