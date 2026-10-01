"""Day boundaries for streaks / reviewed_today follow the learner's timezone.

Reviews inside one UTC day can fall on two Sydney days (UTC+10/11): e.g.
11:00 UTC is late evening in Sydney while 15:00 UTC is already the next
Sydney morning. The old UTC bucketing counted those as one day.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from httpx import AsyncClient
from sqlalchemy import select

import app.db as db
from app.models import ReviewEvent
from tests.conftest import login

SYDNEY = ZoneInfo("Australia/Sydney")


async def review_one_card(client: AsyncClient, headers: dict) -> None:
    queue = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    card = queue["cards"][0]
    await client.post(
        "/api/v1/reviews", json={"card_id": card["card_id"], "rating": 4}, headers=headers
    )


async def set_review_times(*instants: datetime) -> None:
    """Spread the user's review events across the given timestamps."""
    async with db.session_factory() as session:
        events = (await session.scalars(select(ReviewEvent).order_by(ReviewEvent.id))).all()
        assert len(events) >= len(instants)
        for event, ts in zip(events, instants):
            event.reviewed_at = ts
        await session.commit()


async def test_streak_uses_local_days_not_utc(client: AsyncClient) -> None:
    headers = await login(client)
    await review_one_card(client, headers)
    await review_one_card(client, headers)

    # Pick a "now" relative to the fixed instants below rather than real now:
    # both reviews sit in the Sydney days immediately before/at 2026-06-10.
    # 2026-06-09 11:00 UTC == 2026-06-09 21:00 Sydney (AEST, +10)
    # 2026-06-09 15:00 UTC == 2026-06-10 01:00 Sydney — next local day
    await set_review_times(
        datetime(2026, 6, 9, 11, 0, tzinfo=UTC),
        datetime(2026, 6, 9, 15, 0, tzinfo=UTC),
    )

    # Freeze "today" by checking against the data: the streak walk anchors on
    # the current Sydney date, so shift events to end on today's Sydney date.
    today_sydney = datetime.now(UTC).astimezone(SYDNEY).date()
    shift = today_sydney - datetime(2026, 6, 10, 1, 0, tzinfo=SYDNEY).date()
    await set_review_times(
        datetime(2026, 6, 9, 11, 0, tzinfo=UTC) + timedelta(days=shift.days),
        datetime(2026, 6, 9, 15, 0, tzinfo=UTC) + timedelta(days=shift.days),
    )

    body = (await client.get("/api/v1/progress", headers=headers)).json()
    # Two distinct consecutive Sydney days (one of them "today") -> streak 2.
    # Under UTC bucketing both reviews share 2026-06-09 -> streak would be 1.
    assert body["streak_days"] == 2


async def test_reviewed_today_counts_from_local_midnight(client: AsyncClient) -> None:
    headers = await login(client)
    await review_one_card(client, headers)
    await review_one_card(client, headers)

    now = datetime.now(UTC)
    local_midnight = datetime.combine(
        now.astimezone(SYDNEY).date(), datetime.min.time(), tzinfo=SYDNEY
    )
    await set_review_times(
        local_midnight.astimezone(UTC) + timedelta(hours=1),  # today, Sydney time
        local_midnight.astimezone(UTC) - timedelta(hours=1),  # yesterday, Sydney time
    )

    body = (await client.get("/api/v1/progress", headers=headers)).json()
    assert body["reviewed_today"] == 1
