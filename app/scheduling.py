"""FSRS scheduling: the bridge between py-fsrs cards and UserCard rows."""

from datetime import UTC, datetime, timedelta

from fsrs import Card, Rating, Scheduler, State

from app.models import ReviewEvent, UserCard

# Default FSRS parameters, 90% desired retention. Single short learning /
# relearning steps (vs the default 1m+10m): a new card graded Good graduates
# immediately and returns in days, so sessions stay short — the user shadows
# every phrase aloud, so first exposures are deep already (ADR-015). Only
# Again keeps a card cycling within the sitting.
_scheduler = Scheduler(
    learning_steps=(timedelta(minutes=1),),
    relearning_steps=(timedelta(minutes=5),),
)

# New cards introduced per queue fetch. There is no daily cap: every session
# offers another batch, so the learner can study as much as they like; the
# batch size just keeps single sittings digestible.
NEW_CARDS_PER_SESSION = 10


def _card_from_user_card(uc: UserCard) -> Card:
    return Card(
        card_id=uc.id.int & 0x7FFFFFFF,  # py-fsrs wants an int id; not persisted
        state=State(uc.fsrs_state),
        step=uc.fsrs_step,
        stability=uc.stability,
        difficulty=uc.difficulty,
        due=uc.due,
        last_review=uc.last_review,
    )


def apply_review(uc: UserCard, rating: int, now: datetime | None = None) -> ReviewEvent:
    """Run one FSRS review, mutate the UserCard in place, return the log event."""
    now = now or datetime.now(UTC)
    was_new = uc.last_review is None

    card, _log = _scheduler.review_card(_card_from_user_card(uc), Rating(rating), now)

    uc.fsrs_state = card.state.value
    uc.fsrs_step = card.step
    uc.stability = card.stability
    uc.difficulty = card.difficulty
    uc.due = card.due
    uc.last_review = card.last_review
    uc.reps += 1
    if rating == Rating.Again and not was_new:
        uc.lapses += 1

    return ReviewEvent(
        user_id=uc.user_id,
        user_card_id=uc.id,
        rating=rating,
        reviewed_at=now,
        was_new=was_new,
        due_after=uc.due,
        stability_after=uc.stability,
        difficulty_after=uc.difficulty,
    )
