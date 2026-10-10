from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audio import PRONUNCIATION_SCALE, PRONUNCIATION_SCALES, audio_url
from app.auth import get_current_user
from app.db import get_session
from app.glossary import _form_index, record_encounters
from app.models import (
    FSRS_REVIEW_STATE,
    PRODUCTION,
    RECOGNITION,
    Item,
    ItemCategory,
    User,
    UserAnswerAlternative,
    UserCard,
)
from app.sampling import weighted_sample
from app.scheduling import NEW_CARDS_PER_SESSION, apply_review

router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"])

# Cards in learning steps come due minutes later; include them in one sitting.
QUEUE_HORIZON = timedelta(minutes=15)

# Repetition deck weighting (ADR-038). A word the learner keeps missing should
# come round several times as often as one they have solid, but not so much
# that the deck becomes the same handful of words every sitting.
REPEAT_DUE_WEIGHT = 6.0  # due or overdue right now
REPEAT_UNESTABLISHED_WEIGHT = 3.0  # still in learning or relearning steps
REPEAT_LAPSE_WEIGHT = 1.5  # per lapse, capped
REPEAT_LAPSE_CAP = 4
REPEAT_DIFFICULTY_WEIGHT = 2.0  # scaled by FSRS difficulty over its 1-10 range


class CopySpeedOut(BaseModel):
    """One rung of the imitation ladder, slowest first."""

    scale: float
    url: str


class GlossOut(BaseModel):
    word: str  # deck lemma, e.g. "å hete"
    translation: str


class ExampleOut(BaseModel):
    id: int  # lets the client attach voice recordings to this sentence
    sentence: str
    translation: str
    audio_url: str
    # What the learner records against. A different clip from audio_url on
    # purpose: that one varies the speaker for listening practice, this one
    # is always the clearest voice, slowed to a rate a learner can copy
    # (ADR-033). copy_audio_url is the default rung of copy_speeds, kept as
    # its own field so a client that does not offer the chooser still works.
    copy_audio_url: str
    copy_speeds: list[CopySpeedOut]
    # Deck words in the sentence the learner hasn't established yet (the
    # card's own headword excluded) — a helper glossary for the examples.
    gloss: list[GlossOut] = []


class ItemOut(BaseModel):
    id: int
    item_type: str
    source_text: str
    translation: str
    pos: str | None
    cefr_level: str
    categories: list[str]
    audio_url: str
    copy_audio_url: str
    copy_speeds: list[CopySpeedOut]
    examples: list[ExampleOut]


class CardOut(BaseModel):
    card_id: UUID
    direction: str
    is_new: bool
    due: datetime
    item: ItemOut
    # Answers the learner has vouched for on this card; accepted alongside the
    # deck's own wording when their typed answer is checked.
    alternatives: list[str] = []


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


def _in_category(category: str | None):
    """A filter restricting items to one category, or nothing at all."""
    if not category:
        return []
    return [
        Item.id.in_(
            select(ItemCategory.item_id).where(ItemCategory.category == category)
        )
    ]


async def _introduce_new_cards(
    session: AsyncSession,
    user: User,
    language: str,
    allowance: int,
    category: str | None,
    direction: str | None = None,
) -> list[UserCard]:
    """Pick unseen cards up to `allowance`, merging existing unseen rows and
    yet-uncarded items under one ordering by frequency rank.

    A category is a hard restriction, not a sort key. It used to only order the
    pool, which meant a ten-word category silently filled the rest of the deck
    with unrelated words and the category label stopped describing the sitting
    (ADR-038). A category deck that runs out of words is finished; the global
    queue lives in the repetition deck instead.

    Staging: every item starts with a recognition card; a production card becomes
    eligible only once the item's recognition card has graduated to Review state.
    Candidates from both stages interleave by frequency rank.
    """
    if allowance <= 0:
        return []
    scope = _in_category(category)

    unseen_filters = [
        UserCard.user_id == user.id,
        UserCard.last_review.is_(None),
        Item.language_code == language,
        *scope,
    ]
    if direction:
        unseen_filters.append(UserCard.direction == direction)
    unseen = list(
        await session.scalars(
            select(UserCard)
            .join(Item)
            .where(*unseen_filters)
            .order_by(Item.frequency_rank)
            .limit(allowance)
        )
    )

    carded = (
        select(UserCard.item_id)
        .where(UserCard.user_id == user.id, UserCard.direction == RECOGNITION)
        .scalar_subquery()
    )
    rec_rows = await session.execute(
        select(Item.id, Item.frequency_rank)
        .where(Item.language_code == language, Item.id.not_in(carded), *scope)
        .order_by(Item.frequency_rank)
        .limit(allowance)
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
            *scope,
        )
        .order_by(Item.frequency_rank)
        .limit(allowance)
    )

    # Merge unseen rows and would-be new cards under one frequency order; the
    # tiebreak keeps an existing unseen row ahead of creating a fresh card for
    # the same rank.
    pool: list[tuple[tuple, str, object]] = [
        ((uc.item.frequency_rank, 0), "unseen", uc) for uc in unseen
    ]
    if direction in (None, RECOGNITION):
        pool += [((r, 1), RECOGNITION, i) for i, r in rec_rows]
    if direction in (None, PRODUCTION):
        pool += [((r, 2), PRODUCTION, i) for i, r in prod_rows]
    pool.sort(key=lambda entry: entry[0])

    picked: list[UserCard] = []
    created: list[UserCard] = []
    for _k, kind, payload in pool[:allowance]:
        if kind == "unseen":
            picked.append(payload)  # type: ignore[arg-type]
        else:
            card = UserCard(user_id=user.id, item_id=payload, direction=kind)
            session.add(card)
            created.append(card)
            picked.append(card)
    await session.commit()
    for card in created:
        await session.refresh(card)
    return picked


async def _gloss_helper(session: AsyncSession, user_id, language: str, cards: list[UserCard]):
    """Build a lookup of unestablished deck words per example sentence.

    The learner reads example sentences full of words beyond the card itself;
    this glosses the ones the deck knows about but they haven't established.
    """
    index = await _form_index(session, language)
    established = set(
        await session.scalars(
            select(UserCard.item_id).where(
                UserCard.user_id == user_id,
                UserCard.direction == RECOGNITION,
                UserCard.fsrs_state == FSRS_REVIEW_STATE,
            )
        )
    )
    matches: dict[str, set[int]] = {}
    needed: set[int] = set()
    for uc in cards:
        for ex in uc.item.examples:
            if ex.sentence not in matches:
                matches[ex.sentence] = index.match(ex.sentence)
            needed |= matches[ex.sentence]
    info: dict[int, tuple[str, str, int]] = {}
    if needed:
        rows = await session.execute(
            select(Item.id, Item.source_text, Item.translation, Item.frequency_rank).where(
                Item.id.in_(needed)
            )
        )
        info = {i: (word, translation, rank) for i, word, translation, rank in rows}

    def gloss_for(sentence: str, own_item_id: int) -> list[GlossOut]:
        ids = matches.get(sentence, set()) - established - {own_item_id}
        entries = sorted((info[i] for i in ids if i in info), key=lambda e: e[2])
        return [GlossOut(word=word, translation=translation) for word, translation, _ in entries]

    return gloss_for


async def _alternatives(session: AsyncSession, user_id) -> dict[tuple[int, str], list[str]]:
    rows = await session.execute(
        select(
            UserAnswerAlternative.item_id,
            UserAnswerAlternative.direction,
            UserAnswerAlternative.answer,
        ).where(UserAnswerAlternative.user_id == user_id)
    )
    out: dict[tuple[int, str], list[str]] = {}
    for item_id, direction, answer in rows:
        out.setdefault((item_id, direction), []).append(answer)
    return out


def _copy_speeds(language: str, text: str) -> list[CopySpeedOut]:
    return [
        CopySpeedOut(
            scale=scale,
            url=audio_url(language, text, primary=True, scale=scale),
        )
        for scale in PRONUNCIATION_SCALES
    ]


def _card_out(uc: UserCard, gloss_for=None, alternatives=None) -> CardOut:
    item = uc.item
    lang = item.language_code
    return CardOut(
        card_id=uc.id,
        direction=uc.direction,
        is_new=uc.last_review is None,
        due=uc.due,
        alternatives=(alternatives or {}).get((item.id, uc.direction), []),
        item=ItemOut(
            id=item.id,
            item_type=item.item_type,
            source_text=item.source_text,
            translation=item.translation,
            pos=item.pos,
            cefr_level=item.cefr_level,
            categories=sorted(c.category for c in item.categories),
            audio_url=audio_url(lang, item.source_text, primary=True),
            copy_audio_url=audio_url(
                lang, item.source_text, primary=True, scale=PRONUNCIATION_SCALE
            ),
            copy_speeds=_copy_speeds(lang, item.source_text),
            examples=[
                ExampleOut(
                    id=ex.id,
                    sentence=ex.sentence,
                    translation=ex.translation,
                    audio_url=audio_url(lang, ex.sentence),
                    copy_audio_url=audio_url(
                        lang, ex.sentence, primary=True, scale=PRONUNCIATION_SCALE
                    ),
                    copy_speeds=_copy_speeds(lang, ex.sentence),
                    gloss=gloss_for(ex.sentence, item.id) if gloss_for else [],
                )
                for ex in item.examples
            ],
        ),
    )


def _repeat_weight(card: UserCard, due_now: bool) -> float:
    """How strongly this met word wants another look.

    Every met word stays in the pool -- repetition for its own sake is half
    the point -- but the ones the learner's record says are not landing carry
    more weight, so they come round more often until they do.
    """
    weight = 1.0
    if due_now:
        weight += REPEAT_DUE_WEIGHT
    if card.fsrs_state != FSRS_REVIEW_STATE:
        weight += REPEAT_UNESTABLISHED_WEIGHT
    weight += REPEAT_LAPSE_WEIGHT * min(card.lapses, REPEAT_LAPSE_CAP)
    if card.difficulty is not None:
        # FSRS difficulty runs 1-10; normalise so only the hard end pays.
        weight += REPEAT_DIFFICULTY_WEIGHT * max(0.0, (card.difficulty - 1) / 9)
    return weight


async def _repetition_deck(
    session: AsyncSession,
    user: User,
    language: str,
    limit: int,
    direction: str | None,
    scope: list,
) -> tuple[list[UserCard], list[UserCard]]:
    """Every word the learner has met, sampled with the shaky ones favoured.

    Returns (due, extra). Introduces nothing new: this is the deck for keeping
    what is already met, and a sitting here never grows the pile. Due cards
    come first and in due order so FSRS still gets its reviews on time; the
    rest is extra drilling the scheduler would not have asked for, which is
    why it is sampled rather than taken in order -- taking the top N by need
    would show the same words every sitting.
    """
    now = datetime.now(UTC)
    filters = [
        UserCard.user_id == user.id,
        UserCard.last_review.is_not(None),
        Item.language_code == language,
        *scope,
    ]
    if direction:
        filters.append(UserCard.direction == direction)

    # The due split stays in SQL: the stored timestamps' awareness depends on
    # the backend, and comparing them in Python breaks on SQLite.
    due = list(
        await session.scalars(
            select(UserCard)
            .join(Item)
            .where(*filters, UserCard.due <= now + QUEUE_HORIZON)
            .order_by(UserCard.due)
            .limit(limit)
        )
    )
    room = limit - len(due)
    if room <= 0:
        return due, []

    rest = list(
        await session.scalars(
            select(UserCard)
            .join(Item)
            .where(*filters, UserCard.due > now + QUEUE_HORIZON)
        )
    )
    if not rest:
        return due, []
    weights = [_repeat_weight(c, due_now=False) for c in rest]
    return due, [rest[i] for i in weighted_sample(weights, room)]


@router.get("/queue", response_model=QueueOut)
async def queue(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    language: str = "nb",
    limit: int = 15,
    category: str | None = None,
    direction: str | None = None,
    mode: str | None = None,
) -> QueueOut:
    now = datetime.now(UTC)
    scope = _in_category(category)

    if mode == "tricky":
        # Extra practice on words not yet established, strongest first: the
        # ones closest to graduating repeat before the barely-met ones, and
        # scheduling due dates are ignored (reviews still feed FSRS).
        filters = [
            UserCard.user_id == user.id,
            UserCard.last_review.is_not(None),
            UserCard.fsrs_state != FSRS_REVIEW_STATE,
            Item.language_code == language,
            *scope,
        ]
        if direction:
            filters.append(UserCard.direction == direction)
        tricky = list(
            await session.scalars(
                select(UserCard)
                .join(Item)
                .where(*filters)
                .order_by(UserCard.stability.desc().nullslast(), UserCard.due)
                .limit(limit)
            )
        )
        gloss_for = await _gloss_helper(session, user.id, language, tricky)
        alts = await _alternatives(session, user.id)
        return QueueOut(
            cards=[_card_out(c, gloss_for, alts) for c in tricky],
            due_count=len(tricky),
            new_count=0,
        )

    if mode == "repeat":
        due, extra = await _repetition_deck(session, user, language, limit, direction, scope)
        cards = due + extra
        gloss_for = await _gloss_helper(session, user.id, language, cards)
        alts = await _alternatives(session, user.id)
        return QueueOut(
            cards=[_card_out(c, gloss_for, alts) for c in cards],
            due_count=len(due),
            new_count=0,
        )

    due_filters = [
        UserCard.user_id == user.id,
        UserCard.last_review.is_not(None),
        UserCard.due <= now + QUEUE_HORIZON,
        Item.language_code == language,
        *scope,
    ]
    if direction:
        due_filters.append(UserCard.direction == direction)
    due_cards = list(
        await session.scalars(
            select(UserCard)
            .join(Item)
            .where(*due_filters)
            .order_by(UserCard.due)
            .limit(limit)
        )
    )
    allowance = min(NEW_CARDS_PER_SESSION, limit - len(due_cards))
    new_cards = await _introduce_new_cards(session, user, language, allowance, category, direction)

    gloss_for = await _gloss_helper(session, user.id, language, due_cards + new_cards)
    alts = await _alternatives(session, user.id)
    return QueueOut(
        cards=[_card_out(c, gloss_for, alts) for c in due_cards + new_cards],
        due_count=len(due_cards),
        new_count=len(new_cards),
    )


class AlternativeIn(BaseModel):
    card_id: UUID
    answer: str = Field(min_length=1, max_length=256)


@router.post("/alternatives", status_code=201)
async def add_alternative(
    body: AlternativeIn,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    """Accept an answer the checker marked wrong, for this card from now on."""
    uc = await session.get(UserCard, body.card_id)
    if uc is None or uc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Card not found")

    answer = body.answer.strip()
    existing = await session.scalar(
        select(UserAnswerAlternative).where(
            UserAnswerAlternative.user_id == user.id,
            UserAnswerAlternative.item_id == uc.item_id,
            UserAnswerAlternative.direction == uc.direction,
            UserAnswerAlternative.answer == answer,
        )
    )
    if existing is None:
        session.add(
            UserAnswerAlternative(
                user_id=user.id,
                item_id=uc.item_id,
                direction=uc.direction,
                answer=answer,
            )
        )
        await session.commit()
    return {"status": "accepted"}


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
    if event.was_new:
        await record_encounters(session, user.id, uc.item, now)
    await session.commit()

    return ReviewOut(card_id=uc.id, due=uc.due, requeue=uc.due <= now + QUEUE_HORIZON)
