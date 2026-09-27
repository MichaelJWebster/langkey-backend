from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audio import audio_url
from app.auth import get_current_user
from app.content import TOPIC_LABELS
from app.db import get_session
from app.models import (
    FSRS_REVIEW_STATE,
    RECOGNITION,
    Item,
    User,
    UserCard,
    UserGlossaryEntry,
)

router = APIRouter(prefix="/api/v1/glossary", tags=["glossary"])


class GlossaryEntryOut(BaseModel):
    word: str
    translation: str
    pos: str | None
    cefr_level: str
    topic_label: str | None
    first_seen_at: datetime
    via: str  # "card" | "example"
    status: str  # "established" | "learning" | "seen"
    audio_url: str


@router.get("", response_model=list[GlossaryEntryOut])
async def glossary(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    language: str = "nb",
) -> list[GlossaryEntryOut]:
    card_states = {
        item_id: state
        for item_id, state in await session.execute(
            select(UserCard.item_id, UserCard.fsrs_state).where(
                UserCard.user_id == user.id,
                UserCard.direction == RECOGNITION,
                UserCard.last_review.is_not(None),
            )
        )
    }
    entries = await session.scalars(
        select(UserGlossaryEntry)
        .join(Item)
        .where(UserGlossaryEntry.user_id == user.id, Item.language_code == language)
        .order_by(UserGlossaryEntry.first_seen_at.desc())
    )

    def status(item_id: int) -> str:
        state = card_states.get(item_id)
        if state is None:
            return "seen"
        return "established" if state == FSRS_REVIEW_STATE else "learning"

    return [
        GlossaryEntryOut(
            word=e.item.source_text,
            translation=e.item.translation,
            pos=e.item.pos,
            cefr_level=e.item.cefr_level,
            topic_label=TOPIC_LABELS.get(e.item.topic or ""),
            first_seen_at=e.first_seen_at,
            via=e.via,
            status=status(e.item_id),
            audio_url=audio_url(language, e.item.source_text, primary=True),
        )
        for e in entries
    ]
