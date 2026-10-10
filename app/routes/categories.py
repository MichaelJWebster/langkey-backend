"""Vocabulary categories and the learner's true progress through them."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.auth import get_current_user
from app.categories import BLURBS, CATEGORIES, label
from app.db import get_session
from app.models import (
    FSRS_REVIEW_STATE,
    RECOGNITION,
    Item,
    ItemCategory,
    User,
    UserCard,
)

router = APIRouter(prefix="/api/v1/categories", tags=["categories"])


class CategoryOut(BaseModel):
    category: str
    label: str
    blurb: str
    total: int
    remaining: int  # items the learner hasn't been introduced to yet
    established: int  # recognition card graduated to FSRS Review state
    # learning = total - remaining - established (still in learning steps)


class CategoriesOut(BaseModel):
    """Per-category counts plus the honest whole-deck totals.

    `overall` counts distinct items, so the per-category numbers sum to more
    than it wherever a word sits in two categories. That is the point: the
    learner met the word once and it counts as met in both places, but the
    deck is not larger for it.
    """

    categories: list[CategoryOut]
    overall: CategoryOut


@router.get("", response_model=CategoriesOut)
async def categories(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    language: str = "nb",
) -> CategoriesOut:
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
    counts = (
        func.count(),
        func.count().filter(Item.id.not_in(introduced)),
        func.count().filter(Item.id.in_(established)),
    )

    rows = await session.execute(
        select(ItemCategory.category, *counts)
        .join(Item, Item.id == ItemCategory.item_id)
        .where(Item.language_code == language)
        .group_by(ItemCategory.category)
    )
    by_slug = {c: (total, remaining, est) for c, total, remaining, est in rows}

    # Ordered by the canonical category list, not by whatever the group-by
    # returned, so the screen keeps a stable shape as words are added.
    out = [
        CategoryOut(
            category=slug,
            label=label(slug),
            blurb=BLURBS.get(slug, ""),
            total=total,
            remaining=remaining,
            established=est,
        )
        for slug in CATEGORIES
        for total, remaining, est in [by_slug.get(slug, (0, 0, 0))]
    ]

    total, remaining, est = (
        await session.execute(select(*counts).where(Item.language_code == language))
    ).one()
    return CategoriesOut(
        categories=out,
        overall=CategoryOut(
            category="all",
            label="All words",
            blurb="Every A1 word in the deck",
            total=total,
            remaining=remaining,
            established=est,
        ),
    )
