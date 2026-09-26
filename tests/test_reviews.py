from httpx import AsyncClient

from app.scheduling import NEW_CARDS_PER_DAY
from tests.conftest import login


async def test_queue_introduces_new_cards_up_to_daily_limit(client: AsyncClient) -> None:
    headers = await login(client)
    resp = await client.get("/api/v1/reviews/queue", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["due_count"] == 0
    assert body["new_count"] == NEW_CARDS_PER_DAY
    assert all(c["is_new"] for c in body["cards"])
    # First cards follow frequency order: rank 1 is "jeg".
    assert body["cards"][0]["item"]["source_text"] == "jeg"
    assert body["cards"][0]["direction"] == "recognition"


async def test_queue_is_stable_without_reviews(client: AsyncClient) -> None:
    headers = await login(client)
    first = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    second = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    assert [c["card_id"] for c in first["cards"]] == [c["card_id"] for c in second["cards"]]


async def test_review_flow_updates_card_and_counts(client: AsyncClient) -> None:
    headers = await login(client)
    queue = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    card = queue["cards"][0]

    resp = await client.post(
        "/api/v1/reviews", json={"card_id": card["card_id"], "rating": 3}, headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["card_id"] == card["card_id"]
    # Rated Good on first sight: due minutes away (learning step), so requeued.
    assert body["requeue"] is True

    progress = (await client.get("/api/v1/progress", headers=headers)).json()
    assert progress["reviewed_today"] == 1
    assert progress["new_remaining_today"] == NEW_CARDS_PER_DAY - 1
    assert progress["cards_learning"] == 1
    assert progress["streak_days"] == 1


async def test_easy_rating_graduates_card(client: AsyncClient) -> None:
    headers = await login(client)
    queue = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    card = queue["cards"][0]

    resp = await client.post(
        "/api/v1/reviews", json={"card_id": card["card_id"], "rating": 4}, headers=headers
    )
    assert resp.json()["requeue"] is False  # Easy graduates: due days away

    progress = (await client.get("/api/v1/progress", headers=headers)).json()
    assert progress["cards_established"] == 1


async def test_review_rejects_foreign_card(client: AsyncClient) -> None:
    headers = await login(client)
    resp = await client.post(
        "/api/v1/reviews",
        json={"card_id": "00000000-0000-0000-0000-000000000000", "rating": 3},
        headers=headers,
    )
    assert resp.status_code == 404


async def test_progress_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/progress")).status_code == 401
    assert (await client.get("/api/v1/reviews/queue")).status_code == 401
