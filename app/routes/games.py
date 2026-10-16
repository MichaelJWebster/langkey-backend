"""Games: authored sets to play, and the best round the learner has managed.

Deliberately thin. A game keeps one piece of progress -- the best completed
round -- and feeds nothing else: not FSRS, not the CEFR estimate, not the
glossary. Matching ten words against the clock is practice, not evidence of
recall at an interval, and letting it move those numbers would corrupt
figures that currently mean something.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_session
from app.models import GameRound, GameSet, User

router = APIRouter(prefix="/api/v1/games", tags=["games"])


class PairOut(BaseModel):
    en: str
    no: str
    #: Disambiguates where the English alone would not -- "how long" is
    #: `hvor lenge` for time and `hvor langt` for distance.
    note: str | None


class BestOut(BaseModel):
    mistakes: int
    seconds: float


class GameSetOut(BaseModel):
    slug: str
    game: str  # one of GAMES; names the subsection it belongs to
    title: str
    description: str
    pairs: list[PairOut]
    played: int
    #: The best completed round: fewest mistakes, then quickest. Null until
    #: one has been finished.
    best: BestOut | None


class GamesOut(BaseModel):
    sets: list[GameSetOut]


@router.get("", response_model=GamesOut)
async def games(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    language: str = "nb",
) -> GamesOut:
    sets = list(
        await session.scalars(
            select(GameSet)
            .where(GameSet.language_code == language)
            .order_by(GameSet.seq)
        )
    )

    counts = {
        set_id: n
        for set_id, n in await session.execute(
            select(GameRound.set_id, func.count())
            .where(GameRound.user_id == user.id)
            .group_by(GameRound.set_id)
        )
    }
    # Fewest mistakes wins; a tie goes to the quicker round. Done per set in
    # Python because there are a handful of sets and the rule is clearer here
    # than as a window function.
    best: dict[int, GameRound] = {}
    for row in await session.scalars(
        select(GameRound).where(GameRound.user_id == user.id)
    ):
        if row.set_id is None:
            continue
        current = best.get(row.set_id)
        if current is None or (row.mistakes, row.seconds) < (
            current.mistakes,
            current.seconds,
        ):
            best[row.set_id] = row

    return GamesOut(
        sets=[
            GameSetOut(
                slug=s.slug,
                game=s.game,
                title=s.title,
                description=s.description,
                pairs=[
                    PairOut(en=p.en, no=p.no, note=p.note) for p in s.pairs
                ],
                played=counts.get(s.id, 0),
                best=(
                    BestOut(mistakes=b.mistakes, seconds=round(b.seconds, 1))
                    if (b := best.get(s.id))
                    else None
                ),
            )
            for s in sets
        ]
    )


class RoundIn(BaseModel):
    mistakes: int = Field(ge=0)
    seconds: float = Field(gt=0)


@router.post("/{slug}/round")
async def record_round(
    slug: str,
    body: RoundIn,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, int | float]:
    """Record a round the learner finished. Only complete rounds arrive here:
    a set abandoned halfway is not a result, and counting it would make the
    best score a function of how often he gave up."""
    game_set = await session.scalar(select(GameSet).where(GameSet.slug == slug))
    if game_set is None:
        raise HTTPException(404, "No such game set")
    session.add(
        GameRound(
            user_id=user.id,
            set_id=game_set.id,
            pairs=len(game_set.pairs),
            mistakes=body.mistakes,
            seconds=body.seconds,
        )
    )
    await session.commit()
    return {"mistakes": body.mistakes, "seconds": round(body.seconds, 1)}
