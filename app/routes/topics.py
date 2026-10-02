from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.content import TOPIC_LABELS
from app.db import get_session
from app.models import FSRS_REVIEW_STATE, RECOGNITION, Item, User, UserCard

router = APIRouter(prefix="/api/v1/topics", tags=["topics"])


class TopicOut(BaseModel):
    topic: str
    label: str
    total: int
    remaining: int  # items the user hasn't been introduced to yet
    established: int  # recognition card graduated to FSRS Review state
    # learning = total - remaining - established (still in learning steps)


@router.get("", response_model=list[TopicOut])
async def topics(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    language: str = "nb",
) -> list[TopicOut]:
    introduced = (
        select(UserCard.item_id)
        .where(
            UserCard.user_id == user.id,
            UserCard.direction == RECOGNITION,
            UserCard.last_review.is_not(None),
        )
        .scalar_subquery()
    )
    established = (
        select(UserCard.item_id)
        .where(
            UserCard.user_id == user.id,
            UserCard.direction == RECOGNITION,
            UserCard.fsrs_state == FSRS_REVIEW_STATE,
        )
        .scalar_subquery()
    )
    rows = await session.execute(
        select(
            Item.topic,
            func.count(),
            func.count().filter(Item.id.not_in(introduced)),
            func.count().filter(Item.id.in_(established)),
        )
        .where(Item.language_code == language, Item.topic.is_not(None))
        .group_by(Item.topic)
        .order_by(func.min(Item.frequency_rank))
    )
    return [
        TopicOut(
            topic=t,
            label=TOPIC_LABELS.get(t, t.replace("-", " ").title()),
            total=total,
            remaining=remaining,
            established=est,
        )
        for t, total, remaining, est in rows
    ]
