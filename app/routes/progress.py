from datetime import UTC, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_session
from app.models import (
    FSRS_REVIEW_STATE,
    PRODUCTION,
    RECOGNITION,
    Item,
    ReviewEvent,
    User,
    UserCard,
)

router = APIRouter(prefix="/api/v1/progress", tags=["progress"])


class ProgressOut(BaseModel):
    due_now: int
    new_available: int  # unseen cards ready to be introduced (no daily cap)
    reviewed_today: int
    cards_learning: int  # seen but not yet graduated (Learning/Relearning)
    cards_established: int  # graduated to Review state
    total_items: int
    streak_days: int


def _utc_day_start(now: datetime) -> datetime:
    return datetime.combine(now.date(), time.min, tzinfo=UTC)


@router.get("", response_model=ProgressOut)
async def progress(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    language: str = "nb",
) -> ProgressOut:
    now = datetime.now(UTC)
    day_start = _utc_day_start(now)

    seen_cards = (
        select(UserCard)
        .join(Item)
        .where(
            UserCard.user_id == user.id,
            UserCard.last_review.is_not(None),
            Item.language_code == language,
        )
        .subquery()
    )
    due_now = await session.scalar(
        select(func.count()).select_from(seen_cards).where(seen_cards.c.due <= now)
    )
    established = await session.scalar(
        select(func.count())
        .select_from(seen_cards)
        .where(seen_cards.c.fsrs_state == FSRS_REVIEW_STATE)
    )
    learning = await session.scalar(
        select(func.count())
        .select_from(seen_cards)
        .where(seen_cards.c.fsrs_state != FSRS_REVIEW_STATE)
    )
    reviewed_today = await session.scalar(
        select(func.count())
        .select_from(ReviewEvent)
        .where(ReviewEvent.user_id == user.id, ReviewEvent.reviewed_at >= day_start)
    )
    total_items = await session.scalar(
        select(func.count()).select_from(Item).where(Item.language_code == language)
    )

    # New candidates: unseen card rows, items with no recognition card yet, and
    # production cards unlocked by a graduated recognition card.
    unseen = await session.scalar(
        select(func.count())
        .select_from(UserCard)
        .join(Item, UserCard.item_id == Item.id)
        .where(
            UserCard.user_id == user.id,
            UserCard.last_review.is_(None),
            Item.language_code == language,
        )
    )
    rec_carded = (
        select(UserCard.item_id)
        .where(UserCard.user_id == user.id, UserCard.direction == RECOGNITION)
        .scalar_subquery()
    )
    uncarded = await session.scalar(
        select(func.count())
        .select_from(Item)
        .where(Item.language_code == language, Item.id.not_in(rec_carded))
    )
    graduated_rec = (
        select(UserCard.item_id)
        .where(
            UserCard.user_id == user.id,
            UserCard.direction == RECOGNITION,
            UserCard.fsrs_state == FSRS_REVIEW_STATE,
        )
        .scalar_subquery()
    )
    prod_carded = (
        select(UserCard.item_id)
        .where(UserCard.user_id == user.id, UserCard.direction == PRODUCTION)
        .scalar_subquery()
    )
    prod_unlockable = await session.scalar(
        select(func.count())
        .select_from(Item)
        .where(
            Item.language_code == language,
            Item.id.in_(graduated_rec),
            Item.id.not_in(prod_carded),
        )
    )
    new_available = (unseen or 0) + (uncarded or 0) + (prod_unlockable or 0)

    rows = await session.execute(
        select(func.date(ReviewEvent.reviewed_at).label("day"))
        .where(ReviewEvent.user_id == user.id)
        .distinct()
        .order_by(func.date(ReviewEvent.reviewed_at).desc())
    )
    review_dates = [
        d if not isinstance(d, str) else datetime.fromisoformat(d).date() for (d,) in rows
    ]
    # Consecutive UTC days ending today; no review yet today keeps yesterday's streak.
    streak = 0
    expected = now.date()
    for d in review_dates:
        if streak == 0 and d == expected - timedelta(days=1):
            expected = d
        if d != expected:
            break
        streak += 1
        expected -= timedelta(days=1)

    return ProgressOut(
        due_now=due_now or 0,
        new_available=new_available,
        reviewed_today=reviewed_today or 0,
        cards_learning=learning or 0,
        cards_established=established or 0,
        total_items=total_items or 0,
        streak_days=streak,
    )
