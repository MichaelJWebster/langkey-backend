"""Grammar practice: the topic and unit menus, rounds of ten, and answers.

Michael: *"one exercise at a time, completing it gets to the next exercise.
10 exercises before we ask if the user wants to continue, or go back to the
grammar exercises menu page."*

Like the passages, selection is not FSRS. An exercise drills a rule, not a
fact with a recall interval, and the useful order is "what you got wrong last
time, then what you have not done, then the rest". A unit with fewer than ten
exercises repeats some within a round rather than serving a short one.

Feeds nothing else. Grammar has no Council of Europe can-do statement of its
own, so it moves no CEFR ring; the rule is the one Games follows (ADR-042).
"""

import random
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_session
from app.models import GrammarAttempt, GrammarExercise, GrammarUnit, User

router = APIRouter(prefix="/api/v1/grammar", tags=["grammar"])

ROUND_SIZE = 10


class UnitOut(BaseModel):
    slug: str
    title: str
    blurb: str
    review: bool
    total: int  # exercises this unit draws on
    #: Distinct exercises answered at least once, and how many of those were
    #: right the last time. Latest answer wins, as in the passage tally:
    #: getting one right later replaces the miss instead of averaging with it.
    seen: int
    right: int


class TopicOut(BaseModel):
    slug: str
    title: str
    blurb: str
    units: list[UnitOut]


class GrammarMenuOut(BaseModel):
    topics: list[TopicOut]


class ExerciseOut(BaseModel):
    slug: str
    kind: str
    #: The unit the exercise belongs to. In a review round this differs from
    #: the unit being practised, and the UI uses it to point at the right row
    #: of the overview diagram.
    unit: str
    instr: str
    prompt: str | None = None
    cue: str | None = None
    hint: str | None = None
    tiles: list[str] | None = None
    options: list[str] | None = None
    answers: list[str]
    why: str
    ref: str | None = None


class RoundOut(BaseModel):
    topic: str
    unit: str
    title: str
    exercises: list[ExerciseOut]


async def _units(session: AsyncSession, language: str) -> list[GrammarUnit]:
    return list(
        await session.scalars(
            select(GrammarUnit)
            .where(GrammarUnit.language_code == language, GrammarUnit.seq > 0)
            .order_by(GrammarUnit.seq)
        )
    )


async def _latest(session: AsyncSession, user_id) -> dict[int, bool]:
    """Each exercise's most recent outcome for this learner."""
    last = (
        select(
            GrammarAttempt.exercise_id,
            func.max(GrammarAttempt.id).label("last_id"),
        )
        .where(GrammarAttempt.user_id == user_id)
        .group_by(GrammarAttempt.exercise_id)
        .subquery()
    )
    rows = await session.execute(
        select(GrammarAttempt.exercise_id, GrammarAttempt.correct).join(
            last, last.c.last_id == GrammarAttempt.id
        )
    )
    return {ex_id: correct for ex_id, correct in rows if ex_id is not None}


def _pool(
    unit: GrammarUnit,
    units: list[GrammarUnit],
    by_unit: dict[int, list[GrammarExercise]],
) -> list[GrammarExercise]:
    """The exercises a unit draws on: its own, or for a review, its topic's."""
    if not unit.review:
        return by_unit.get(unit.id, [])
    return [
        ex
        for u in units
        if u.topic == unit.topic and not u.review
        for ex in by_unit.get(u.id, [])
    ]


async def _exercises_by_unit(
    session: AsyncSession, units: list[GrammarUnit]
) -> dict[int, list[GrammarExercise]]:
    by_unit: dict[int, list[GrammarExercise]] = {}
    if not units:
        return by_unit
    for ex in await session.scalars(
        select(GrammarExercise)
        .where(
            GrammarExercise.unit_id.in_([u.id for u in units]),
            GrammarExercise.seq > 0,
        )
        .order_by(GrammarExercise.unit_id, GrammarExercise.seq)
    ):
        by_unit.setdefault(ex.unit_id, []).append(ex)
    return by_unit


@router.get("", response_model=GrammarMenuOut)
async def grammar_menu(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    language: str = "nb",
) -> GrammarMenuOut:
    units = await _units(session, language)
    by_unit = await _exercises_by_unit(session, units)
    latest = await _latest(session, user.id)

    topics: dict[str, TopicOut] = {}
    for u in units:
        pool = _pool(u, units, by_unit)
        answered = [latest[ex.id] for ex in pool if ex.id in latest]
        topic = topics.setdefault(
            u.topic,
            TopicOut(slug=u.topic, title=u.topic_title, blurb=u.topic_blurb, units=[]),
        )
        topic.units.append(
            UnitOut(
                slug=u.unit,
                title=u.title,
                blurb=u.blurb,
                review=u.review,
                total=len(pool),
                seen=len(answered),
                right=sum(answered),
            )
        )
    return GrammarMenuOut(topics=list(topics.values()))


def order_round(
    pool: list[GrammarExercise],
    latest: dict[int, bool],
    size: int,
    rng: random.Random,
) -> list[GrammarExercise]:
    """Missed last time first, then never answered, then the ones got right.

    Shuffled within each group so a round is not the authored order every
    time. Topped up to `size` from fresh shuffles of the whole pool, never
    putting the same exercise twice in a row.
    """
    missed = [ex for ex in pool if latest.get(ex.id) is False]
    unseen = [ex for ex in pool if ex.id not in latest]
    right = [ex for ex in pool if latest.get(ex.id) is True]
    for group in (missed, unseen, right):
        rng.shuffle(group)
    ordered = missed + unseen + right
    while ordered and len(ordered) < size:
        more = pool[:]
        rng.shuffle(more)
        if len(more) > 1 and more[0].id == ordered[-1].id:
            more.append(more.pop(0))
        ordered += more
    return ordered[:size]


@router.get("/{topic}/{unit}/round", response_model=RoundOut)
async def grammar_round(
    topic: str,
    unit: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    language: str = "nb",
    limit: int = ROUND_SIZE,
) -> RoundOut:
    units = await _units(session, language)
    target = next((u for u in units if u.topic == topic and u.unit == unit), None)
    if target is None:
        raise HTTPException(404, "No such grammar unit")
    by_unit = await _exercises_by_unit(session, units)
    unit_slug = {u.id: u.unit for u in units}
    pool = _pool(target, units, by_unit)
    chosen = order_round(
        pool, await _latest(session, user.id), max(1, min(limit, 50)), random.Random()
    )
    return RoundOut(
        topic=topic,
        unit=unit,
        title=target.title,
        exercises=[
            ExerciseOut(slug=ex.slug, kind=ex.kind, unit=unit_slug[ex.unit_id], **ex.data)
            for ex in chosen
        ],
    )


class AttemptIn(BaseModel):
    slug: str
    correct: bool
    answer: str | None = Field(default=None, max_length=1000)


@router.post("/attempts")
async def record_attempt(
    body: AttemptIn,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str | bool]:
    exercise = await session.scalar(
        select(GrammarExercise).where(GrammarExercise.slug == body.slug)
    )
    if exercise is None:
        raise HTTPException(404, "No such exercise")
    session.add(
        GrammarAttempt(
            user_id=user.id,
            exercise_id=exercise.id,
            correct=body.correct,
            answer=body.answer,
        )
    )
    await session.commit()
    return {"slug": body.slug, "correct": body.correct}
