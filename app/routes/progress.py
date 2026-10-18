from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.cefr import estimate
from app.config import get_settings
from app.db import get_session
from app.models import (
    FSRS_REVIEW_STATE,
    PRODUCTION,
    RECOGNITION,
    ExerciseAttempt,
    Item,
    PronunciationAttempt,
    ReviewEvent,
    User,
    UserCard,
    UserRecording,
)
from app.models.reading import ReadingText, Sentence, UserReadingPosition
from app.routes.exercises import _attempt_score
from app.routes.passages import Tally, mark_tally, reading_credit

router = APIRouter(prefix="/api/v1/progress", tags=["progress"])


class ProgressOut(BaseModel):
    due_now: int
    new_available: int  # unseen cards ready to be introduced (no daily cap)
    reviewed_today: int
    cards_learning: int  # seen but not yet graduated (Learning/Relearning)
    cards_established: int  # graduated to Review state
    total_items: int
    streak_days: int


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def _local_day_start(now: datetime) -> datetime:
    """Start of the learner's current wall-clock day, as a UTC instant.

    Normalised to UTC so the DB comparison works on SQLite too, where
    datetimes compare as strings and a non-UTC offset would break ordering.
    """
    local_midnight = datetime.combine(now.astimezone(_tz()).date(), time.min, tzinfo=_tz())
    return local_midnight.astimezone(UTC)


def _local_date(ts: datetime | str) -> date:
    """A review timestamp's date on the learner's wall clock (storage is UTC)."""
    if isinstance(ts, str):  # SQLite returns ISO strings
        ts = datetime.fromisoformat(ts)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(_tz()).date()


@router.get("", response_model=ProgressOut)
async def progress(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    language: str = "nb",
) -> ProgressOut:
    now = datetime.now(UTC)
    day_start = _local_day_start(now)

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

    # Local-day bucketing must happen in Python: SQL date() works in UTC, and
    # the learner's offset shifts with DST. Volume is one row per review.
    rows = await session.execute(
        select(ReviewEvent.reviewed_at)
        .where(ReviewEvent.user_id == user.id)
        .order_by(ReviewEvent.reviewed_at.desc())
    )
    review_dates = sorted({_local_date(ts) for (ts,) in rows}, reverse=True)
    # Consecutive local days ending today; no review yet today keeps yesterday's streak.
    streak = 0
    expected = now.astimezone(_tz()).date()
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


class CardBreakdown(BaseModel):
    unseen: int
    learning: int  # seen, still in learning steps
    relearning: int  # lapsed and being relearned
    established: int  # graduated to FSRS Review state


class ListeningBreakdown(BaseModel):
    clean: int  # full marks, no hints
    helped: int  # right with hints / partial credit
    missed: int
    total: int  # attempts considered (recent window)


class ReadingBreakdown(BaseModel):
    sentences_read: int
    sentences_total: int
    #: The A1 translation passages -- the only part of Reading that is marked.
    #: The library is read, not scored, so it has no percentage and should not
    #: be given one.
    passages: Tally


class SkillsBreakdownOut(BaseModel):
    vocabulary: CardBreakdown  # recognition cards over the whole deck
    writing: CardBreakdown  # production cards over unlocked items
    listening: ListeningBreakdown
    reading: ReadingBreakdown


async def _card_breakdown(
    session: AsyncSession, user_id, language: str, direction: str, total_pool: int
) -> CardBreakdown:
    rows = (
        await session.execute(
            select(UserCard.fsrs_state, func.count())
            .join(Item)
            .where(
                UserCard.user_id == user_id,
                UserCard.direction == direction,
                UserCard.last_review.is_not(None),
                Item.language_code == language,
            )
            .group_by(UserCard.fsrs_state)
        )
    ).all()
    by_state = dict(rows)
    seen = sum(by_state.values())
    return CardBreakdown(
        unseen=max(0, total_pool - seen),
        learning=sum(n for s, n in by_state.items() if s not in (FSRS_REVIEW_STATE, 3)),
        relearning=by_state.get(3, 0),
        established=by_state.get(FSRS_REVIEW_STATE, 0),
    )


@router.get("/skills", response_model=SkillsBreakdownOut)
async def skills_breakdown(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    language: str = "nb",
) -> SkillsBreakdownOut:
    """Per-skill state counts for the practice screens' progress graphics."""
    total_items = (
        await session.scalar(
            select(func.count()).select_from(Item).where(Item.language_code == language)
        )
    ) or 0
    # Production cards unlock when the recognition card graduates.
    unlocked = (
        await session.scalar(
            select(func.count())
            .select_from(UserCard)
            .join(Item)
            .where(
                UserCard.user_id == user.id,
                UserCard.direction == RECOGNITION,
                UserCard.fsrs_state == FSRS_REVIEW_STATE,
                Item.language_code == language,
            )
        )
    ) or 0

    vocabulary = await _card_breakdown(session, user.id, language, RECOGNITION, total_items)
    writing = await _card_breakdown(session, user.id, language, PRODUCTION, unlocked)

    recent = (
        await session.execute(
            select(ExerciseAttempt.correct, ExerciseAttempt.hints, ExerciseAttempt.score)
            .where(
                ExerciseAttempt.user_id == user.id,
                ExerciseAttempt.exercise_type == "dictation",
            )
            .order_by(ExerciseAttempt.created_at.desc())
            .limit(30)
        )
    ).all()
    clean = helped = missed = 0
    for c, h, s in recent:
        mark = _attempt_score(c, h, s)
        if mark >= 1:
            clean += 1
        elif mark > 0:
            helped += 1
        else:
            missed += 1

    sentences_total = (
        await session.scalar(
            select(func.count())
            .select_from(Sentence)
            .join(ReadingText, Sentence.text_id == ReadingText.id)
            .where(ReadingText.language_code == language)
        )
    ) or 0
    sentences_read = (
        await session.scalar(
            select(func.coalesce(func.sum(UserReadingPosition.seq), 0))
            .select_from(UserReadingPosition)
            .join(ReadingText, UserReadingPosition.text_id == ReadingText.id)
            .where(
                UserReadingPosition.user_id == user.id,
                ReadingText.language_code == language,
            )
        )
    ) or 0

    return SkillsBreakdownOut(
        vocabulary=vocabulary,
        writing=writing,
        listening=ListeningBreakdown(clean=clean, helped=helped, missed=missed, total=len(recent)),
        reading=ReadingBreakdown(
            sentences_read=sentences_read,
            sentences_total=sentences_total,
            passages=await mark_tally(session, user.id, language),
        ),
    )


class SkillOut(BaseModel):
    skill: str  # vocabulary | listening | reading | writing | speaking | conversation
    practised: bool
    level: str | None  # highest band cleared; None = pre-A1
    next_level: str | None
    progress: float  # 0..1 toward next_level
    detail: str


class CefrOut(BaseModel):
    overall_level: str | None  # weakest practised skill — CEFR needs them all
    overall_next: str | None
    overall_progress: float
    skills: list[SkillOut]
    note: str


async def _established(session: AsyncSession, user_id, language: str, direction: str) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(UserCard)
            .join(Item)
            .where(
                UserCard.user_id == user_id,
                UserCard.direction == direction,
                UserCard.fsrs_state == FSRS_REVIEW_STATE,
                Item.language_code == language,
            )
        )
    ) or 0


# Speaking (ADR-044). How many recent words the clarity rate is taken over:
# enough that one bad take is noise, few enough that it tracks improvement --
# the same job the last 20 dictations do for listening. 200 words is about
# five sessions of eight phrases.
SPEAKING_WINDOW = 200
# Checked phrases before clarity counts in full, so a single good take cannot
# fill the ring -- the same job 300 sentences do for reading.
SPEAKING_SATURATION = 100


async def _speaking(session: AsyncSession, user_id, language: str) -> tuple[float, int]:
    """Recent clarity and how many takes have been checked.

    Clarity is the share of the last SPEAKING_WINDOW target words that the
    speech model found exactly -- the same exact-match rule the per-word tally
    uses, so a slip like klokka -> klokke counts against (ADR-036).
    """
    recent = (
        select(PronunciationAttempt.said)
        .where(
            PronunciationAttempt.user_id == user_id,
            PronunciationAttempt.language_code == language,
        )
        .order_by(PronunciationAttempt.created_at.desc(), PronunciationAttempt.id.desc())
        .limit(SPEAKING_WINDOW)
        .subquery()
    )
    words, said = (
        await session.execute(
            select(func.count(), func.count().filter(recent.c.said)).select_from(recent)
        )
    ).one()
    takes = (
        await session.scalar(
            select(func.count())
            .select_from(UserRecording)
            .join(Item, UserRecording.item_id == Item.id)
            .where(
                UserRecording.user_id == user_id,
                UserRecording.transcript.is_not(None),
                Item.language_code == language,
            )
        )
    ) or 0
    return (said / words if words else 0.0), takes


def _reading_detail(sentences_read: int, passages_done: int) -> str:
    """The line under the reading ring. Names both halves when both exist, so
    it is obvious which activity moved the number."""
    parts = []
    if passages_done:
        parts.append(f"{passages_done} passages translated")
    if sentences_read:
        parts.append(f"{sentences_read} library sentences read")
    return " · ".join(parts) if parts else "no reading yet"


@router.get("/cefr", response_model=CefrOut)
async def cefr_progress(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    language: str = "nb",
) -> CefrOut:
    """Estimate CEFR progress per skill from an "effective vocabulary".

    Established recognition cards anchor the estimate; comprehension skills
    discount that vocabulary by how well it holds up in actual listening and
    how much connected text has been read. See app.cefr for the ladder.
    """
    vocab = await _established(session, user.id, language, RECOGNITION)
    prod_vocab = await _established(session, user.id, language, PRODUCTION)

    recent = (
        await session.execute(
            select(ExerciseAttempt.correct, ExerciseAttempt.hints, ExerciseAttempt.score)
            .where(
                ExerciseAttempt.user_id == user.id,
                ExerciseAttempt.exercise_type == "dictation",
            )
            .order_by(ExerciseAttempt.created_at.desc())
            .limit(20)
        )
    ).all()
    listen_mark = (
        sum(_attempt_score(c, h, s) for c, h, s in recent) / len(recent) if recent else 0.0
    )

    sentences_read = (
        await session.scalar(
            select(func.coalesce(func.sum(UserReadingPosition.seq), 0))
            .select_from(UserReadingPosition)
            .join(ReadingText, UserReadingPosition.text_id == ReadingText.id)
            .where(
                UserReadingPosition.user_id == user.id,
                ReadingText.language_code == language,
            )
        )
    ) or 0
    # Translating an A1 passage is reading too -- more actively than following
    # an interleaved novel, since the learner has to produce the meaning and
    # then mark it. Michael had 28 passages behind him and the ring still said
    # "no reading yet", because this counted only the library. Weighted by how
    # each passage went, so a passage marked "missed" earns nothing.
    translated, passages_done = await reading_credit(session, user.id, language)

    # Reading comfort saturates once a few hundred sentences of connected
    # text have been read; below that it discounts the vocabulary estimate.
    reading_factor = min(1.0, (sentences_read + translated) / 300)

    # Speaking is production, so it starts from the words recalled from
    # English -- the writing vocabulary -- discounted by how clearly they come
    # out when said. Pronunciation only: LangKey does not train conversation
    # yet, and this cannot see it.
    clarity, takes = await _speaking(session, user.id, language)
    speaking_factor = clarity * min(1.0, takes / SPEAKING_SATURATION)

    effectives: dict[str, tuple[float, bool, str]] = {
        "vocabulary": (vocab, vocab > 0, f"{vocab} words established"),
        "listening": (
            vocab * listen_mark,
            bool(recent),
            f"{len(recent)} recent dictations at {round(listen_mark * 100)}%"
            if recent
            else "no listening practice yet",
        ),
        "reading": (
            vocab * reading_factor,
            sentences_read > 0 or passages_done > 0,
            _reading_detail(sentences_read, passages_done),
        ),
        "writing": (
            prod_vocab,
            prod_vocab > 0,
            f"{prod_vocab} words produced from English",
        ),
        "speaking": (
            prod_vocab * speaking_factor,
            takes > 0,
            f"{round(clarity * 100)}% of recent words heard clearly · {takes} phrases checked"
            if takes
            else "no pronunciation practice yet",
        ),
    }

    skills = []
    for name, (effective, practised, detail) in effectives.items():
        level, next_level, progress = estimate(effective)
        skills.append(
            SkillOut(
                skill=name,
                practised=practised,
                level=level,
                next_level=next_level,
                progress=round(progress, 3),
                detail=detail,
            )
        )
    # Spoken interaction, separate from spoken production as CEFR has it
    # (ADR-045). Nothing trains it yet, so it is reported but not estimated,
    # and it stays out of the overall level below: a skill the app cannot
    # practise would pin the overall at zero and say nothing about him.
    skills.append(
        SkillOut(
            skill="conversation",
            practised=False,
            level=None,
            next_level="A1",
            progress=0.0,
            detail="not trained in LangKey yet",
        )
    )

    # A CEFR level requires every skill, so the overall estimate follows the
    # weakest one the app trains -- speaking included, conversation not yet.
    weakest = min((effectives[n][0] for n in effectives), default=0.0)
    overall_level, overall_next, overall_progress = estimate(weakest)

    return CefrOut(
        overall_level=overall_level,
        overall_next=overall_next,
        overall_progress=round(overall_progress, 3),
        skills=skills,
        note=(
            "Estimated from vocabulary research (Milton & Alexiou), not a "
            "certification — the official test is Norskprøven (prove.hkdir.no)."
        ),
    )
