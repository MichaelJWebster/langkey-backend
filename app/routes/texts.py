"""Reading library: interleaved Norwegian/English texts."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_session
from app.models import (
    ReadingText,
    Sentence,
    SentenceTranslation,
    User,
    UserReadingPosition,
)

router = APIRouter(prefix="/api/v1/texts", tags=["texts"])

PAGE_SIZE = 100  # sentences per fetch


class TextOut(BaseModel):
    slug: str
    title: str
    author: str | None
    kind: str
    orthography: str
    license: str | None
    sentence_count: int
    position: int  # last read seq, 0 if unstarted


class SentenceOut(BaseModel):
    seq: int
    paragraph: int
    no: str
    en: str


class TextPageOut(BaseModel):
    slug: str
    title: str
    author: str | None
    from_seq: int
    to_seq: int
    sentence_count: int
    sentences: list[SentenceOut]


@router.get("", response_model=list[TextOut])
async def list_texts(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    language: str = "nb",
) -> list[TextOut]:
    counts = (
        select(Sentence.text_id, func.count().label("n"))
        .group_by(Sentence.text_id)
        .subquery()
    )
    rows = await session.execute(
        select(ReadingText, counts.c.n, UserReadingPosition.seq)
        .join(counts, counts.c.text_id == ReadingText.id)
        .outerjoin(
            UserReadingPosition,
            (UserReadingPosition.text_id == ReadingText.id)
            & (UserReadingPosition.user_id == user.id),
        )
        .where(ReadingText.language_code == language)
        .order_by(ReadingText.title)
    )
    return [
        TextOut(
            slug=t.slug,
            title=t.title,
            author=t.author,
            kind=t.kind,
            orthography=t.orthography,
            license=t.license,
            sentence_count=n,
            position=pos or 0,
        )
        for t, n, pos in rows
    ]


@router.get("/{slug}", response_model=TextPageOut)
async def text_page(
    slug: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    from_seq: int = 1,
) -> TextPageOut:
    text = await session.scalar(select(ReadingText).where(ReadingText.slug == slug))
    if not text:
        raise HTTPException(404, "No such text")
    total = await session.scalar(
        select(func.count()).select_from(Sentence).where(Sentence.text_id == text.id)
    )
    rows = await session.execute(
        select(Sentence.seq, Sentence.paragraph, Sentence.no_text, SentenceTranslation.en_text)
        .join(
            SentenceTranslation,
            (SentenceTranslation.sentence_id == Sentence.id)
            & SentenceTranslation.is_current,
        )
        .where(Sentence.text_id == text.id, Sentence.seq >= from_seq)
        .order_by(Sentence.seq)
        .limit(PAGE_SIZE)
    )
    sentences = [
        SentenceOut(seq=seq, paragraph=par, no=no, en=en) for seq, par, no, en in rows
    ]
    return TextPageOut(
        slug=text.slug,
        title=text.title,
        author=text.author,
        from_seq=from_seq,
        to_seq=sentences[-1].seq if sentences else from_seq,
        sentence_count=total or 0,
        sentences=sentences,
    )


class PositionIn(BaseModel):
    seq: int


@router.put("/{slug}/position")
async def save_position(
    slug: str,
    body: PositionIn,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, int]:
    text = await session.scalar(select(ReadingText).where(ReadingText.slug == slug))
    if not text:
        raise HTTPException(404, "No such text")
    row = await session.get(UserReadingPosition, (user.id, text.id))
    if row:
        row.seq = max(row.seq, body.seq)  # never move backwards implicitly
    else:
        row = UserReadingPosition(user_id=user.id, text_id=text.id, seq=body.seq)
        session.add(row)
    await session.commit()
    return {"seq": row.seq}
