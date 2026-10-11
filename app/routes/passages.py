"""A1 translation passages: serve them, and record the learner's own marking.

Selection is deliberately not FSRS. A passage is not a card: there is no
recall interval to model, and the useful order is simply "what you have not
done, then what you got wrong". Sorting happens in Python because the whole
set is a few hundred rows and the rule is easier to read than the SQL for it.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_session
from app.models import MARKS, Passage, PassageAttempt, User

router = APIRouter(prefix="/api/v1/passages", tags=["passages"])

SESSION_SIZE = 10

#: How many passages that went badly may open a session. The point of a cap is
#: that a bad day does not become a wall of its own failures next time: the
#: session leads with a few of them and then moves on to new material. Same
#: idea as REPEAT_LAPSE_CAP in the repetition deck.
REVIEW_SLOTS = 3

#: Lower sorts earlier among the rest. Unseen passages are the forward
#: progress; one already marked "got" is last but never retired, so the
#: section does not run dry.
MARK_PRIORITY = {None: 0, "missed": 1, "partly": 2, "got": 3}


class GlossOut(BaseModel):
    form: str
    lemma: str
    en: str


class PassageOut(BaseModel):
    slug: str
    kind: str
    level: str
    no: str
    en: str  # the reference translation, revealed by the UI on request
    word_count: int
    topics: list[str]
    glossary: list[GlossOut]
    last_mark: str | None
    attempts: int


class PassageSessionOut(BaseModel):
    total: int
    attempted: int  # distinct passages marked at least once
    got: int  # distinct passages whose latest mark is "got"
    passages: list[PassageOut]


@router.get("", response_model=PassageSessionOut)
async def passage_session(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    language: str = "nb",
    topic: str | None = None,
    limit: int = SESSION_SIZE,
) -> PassageSessionOut:
    rows = list(
        await session.scalars(
            select(Passage)
            .where(Passage.language_code == language)
            .order_by(Passage.seq)
        )
    )
    if topic:
        rows = [p for p in rows if any(t.topic == topic for t in p.topics)]

    # Latest mark and attempt count per passage, for this user only.
    latest = (
        select(
            PassageAttempt.passage_id,
            func.count().label("n"),
            func.max(PassageAttempt.id).label("last_id"),
        )
        .where(PassageAttempt.user_id == user.id)
        .group_by(PassageAttempt.passage_id)
        .subquery()
    )
    history = {
        pid: (mark, n)
        for pid, n, mark in await session.execute(
            select(latest.c.passage_id, latest.c.n, PassageAttempt.mark).join(
                PassageAttempt, PassageAttempt.id == latest.c.last_id
            )
        )
    }

    def mark_of(p: Passage) -> str | None:
        return history.get(p.id, (None, 0))[0]

    def sort_key(p: Passage) -> tuple[int, int]:
        return (MARK_PRIORITY.get(mark_of(p), 0), p.seq)

    limit = max(1, limit)
    # Lead with a few that went badly, then work forward through the rest.
    review = [p for p in rows if mark_of(p) in ("missed", "partly")][:REVIEW_SLOTS]
    rest = [p for p in sorted(rows, key=sort_key) if p not in review]
    ordered = (review + rest)[:limit]
    return PassageSessionOut(
        total=len(rows),
        attempted=sum(1 for p in rows if p.id in history),
        got=sum(1 for p in rows if history.get(p.id, (None, 0))[0] == "got"),
        passages=[
            PassageOut(
                slug=p.slug,
                kind=p.kind,
                level=p.level,
                no=p.no_text,
                en=p.en_text,
                word_count=p.word_count,
                topics=sorted(t.topic for t in p.topics),
                glossary=[
                    GlossOut(form=g.form, lemma=g.lemma, en=g.en) for g in p.glosses
                ],
                last_mark=history.get(p.id, (None, 0))[0],
                attempts=history.get(p.id, (None, 0))[1],
            )
            for p in ordered
        ],
    )


class AttemptIn(BaseModel):
    mark: str
    answer: str | None = Field(default=None, max_length=4000)
    revealed: bool = True


@router.post("/{slug}/attempt")
async def record_attempt(
    slug: str,
    body: AttemptIn,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str | int]:
    if body.mark not in MARKS:
        raise HTTPException(422, f"mark must be one of {', '.join(MARKS)}")
    passage = await session.scalar(select(Passage).where(Passage.slug == slug))
    if passage is None:
        raise HTTPException(404, "No such passage")
    session.add(
        PassageAttempt(
            user_id=user.id,
            passage_id=passage.id,
            mark=body.mark,
            answer=body.answer,
            revealed=body.revealed,
        )
    )
    await session.commit()
    return {"slug": slug, "mark": body.mark, "score": MARKS.index(body.mark)}
