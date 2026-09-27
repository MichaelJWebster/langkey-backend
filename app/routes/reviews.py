from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audio import audio_url
from app.auth import get_current_user
from app.db import get_session
from app.models import FSRS_REVIEW_STATE, PRODUCTION, RECOGNITION, Item, User, UserCard
from app.scheduling import NEW_CARDS_PER_SESSION, apply_review

router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"])

# Cards in learning steps come due minutes later; include them in one sitting.
QUEUE_HORIZON = timedelta(minutes=15)


class ExampleOut(BaseModel):
    sentence: str
    translation: str
    audio_url: str


class ItemOut(BaseModel):
    id: int
    item_type: str
    source_text: str
    translation: str
    pos: str | None
    cefr_level: str
    audio_url: str
    examples: list[ExampleOut]


class CardOut(BaseModel):
    card_id: UUID
    direction: str
    is_new: bool
    due: datetime
    item: ItemOut


class QueueOut(BaseModel):
    cards: list[CardOut]
    due_count: int
    new_count: int


class ReviewIn(BaseModel):
    card_id: UUID
    rating: int = Field(ge=1, le=4)  # fsrs.Rating: Again/Hard/Good/Easy


class ReviewOut(BaseModel):
    card_id: UUID
    due: datetime
    requeue: bool  # true if the card comes due again within this sitting


async def _introduce_new_cards(
    session: AsyncSession, user: User, language: str, allowance: int
) -> list[UserCard]:
    """Pick unseen cards up to `allowance`: existing unseen rows first, then new rows.

    Staging: every item starts with a recognition card; a production card becomes
    eligible only once the item's recognition card has graduated to Review state.
    Candidates from both stages interleave by frequency rank.
    """
    if allowance <= 0:
        return []

    unseen = list(
        await session.scalars(
            select(UserCard)
            .join(Item)
            .where(
                UserCard.user_id == user.id,
                UserCard.last_review.is_(None),
                Item.language_code == language,
            )
            .order_by(Item.frequency_rank)
            .limit(allowance)
        )
    )
    needed = allowance - len(unseen)
    if needed <= 0:
        return unseen

    carded = (
        select(UserCard.item_id)
        .where(UserCard.user_id == user.id, UserCard.direction == RECOGNITION)
        .scalar_subquery()
    )
    rec_rows = await session.execute(
        select(Item.id, Item.frequency_rank)
        .where(Item.language_code == language, Item.id.not_in(carded))
        .order_by(Item.frequency_rank)
        .limit(needed)
    )
    graduated_recognition = (
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
    prod_rows = await session.execute(
        select(Item.id, Item.frequency_rank)
        .where(
            Item.language_code == language,
            Item.id.in_(graduated_recognition),
            Item.id.not_in(prod_carded),
        )
        .order_by(Item.frequency_rank)
        .limit(needed)
    )

    candidates = [(rank, item_id, RECOGNITION) for item_id, rank in rec_rows] + [
        (rank, item_id, PRODUCTION) for item_id, rank in prod_rows
    ]
    candidates.sort()

    created: list[UserCard] = []
    for _rank, item_id, direction in candidates[:needed]:
        card = UserCard(user_id=user.id, item_id=item_id, direction=direction)
        session.add(card)
        created.append(card)
    await session.commit()
    for card in created:
        await session.refresh(card)
    return unseen + created


def _card_out(uc: UserCard) -> CardOut:
    item = uc.item
    lang = item.language_code
    return CardOut(
        card_id=uc.id,
        direction=uc.direction,
        is_new=uc.last_review is None,
        due=uc.due,
        item=ItemOut(
            id=item.id,
            item_type=item.item_type,
            source_text=item.source_text,
            translation=item.translation,
            pos=item.pos,
            cefr_level=item.cefr_level,
            audio_url=audio_url(lang, item.source_text),
            examples=[
                ExampleOut(
                    sentence=ex.sentence,
                    translation=ex.translation,
                    audio_url=audio_url(lang, ex.sentence),
                )
                for ex in item.examples
            ],
        ),
    )


@router.get("/queue", response_model=QueueOut)
async def queue(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    language: str = "nb",
    limit: int = 30,
) -> QueueOut:
    now = datetime.now(UTC)
    due_cards = list(
        await session.scalars(
            select(UserCard)
            .join(Item)
            .where(
                UserCard.user_id == user.id,
                UserCard.last_review.is_not(None),
                UserCard.due <= now + QUEUE_HORIZON,
                Item.language_code == language,
            )
            .order_by(UserCard.due)
            .limit(limit)
        )
    )
    allowance = min(NEW_CARDS_PER_SESSION, limit - len(due_cards))
    new_cards = await _introduce_new_cards(session, user, language, allowance)

    return QueueOut(
        cards=[_card_out(c) for c in due_cards + new_cards],
        due_count=len(due_cards),
        new_count=len(new_cards),
    )


@router.post("", response_model=ReviewOut)
async def submit_review(
    body: ReviewIn,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReviewOut:
    uc = await session.get(UserCard, body.card_id)
    if uc is None or uc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Card not found")

    now = datetime.now(UTC)
    event = apply_review(uc, body.rating, now)
    session.add(event)
    await session.commit()

    return ReviewOut(card_id=uc.id, due=uc.due, requeue=uc.due <= now + QUEUE_HORIZON)
