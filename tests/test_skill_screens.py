"""Queue variants and breakdown endpoints behind the skills dashboard."""

from httpx import AsyncClient

from tests.conftest import login


async def test_queue_defaults_to_15_and_filters_direction(client: AsyncClient) -> None:
    headers = await login(client)

    queue = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    assert len(queue["cards"]) <= 15

    prod = (await client.get("/api/v1/reviews/queue?direction=production", headers=headers)).json()
    assert all(c["direction"] == "production" for c in prod["cards"])
    # Nothing graduated yet, so no production cards exist or unlock.
    assert prod["cards"] == []

    rec = (await client.get("/api/v1/reviews/queue?direction=recognition", headers=headers)).json()
    assert rec["cards"] and all(c["direction"] == "recognition" for c in rec["cards"])


async def test_tricky_mode_returns_seen_unestablished_cards(client: AsyncClient) -> None:
    headers = await login(client)

    empty = (await client.get("/api/v1/reviews/queue?mode=tricky", headers=headers)).json()
    assert empty["cards"] == []  # nothing seen yet

    queue = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    for card in queue["cards"][:3]:
        await client.post(
            "/api/v1/reviews", json={"card_id": card["card_id"], "rating": 1}, headers=headers
        )

    tricky = (await client.get("/api/v1/reviews/queue?mode=tricky", headers=headers)).json()
    assert 1 <= len(tricky["cards"]) <= 15
    assert tricky["new_count"] == 0
    assert all(not c["is_new"] for c in tricky["cards"])


async def test_topics_report_established_counts(client: AsyncClient) -> None:
    headers = await login(client)
    topics = (await client.get("/api/v1/topics", headers=headers)).json()
    assert topics
    for t in topics:
        assert t["established"] == 0  # fresh account
        assert 0 <= t["remaining"] <= t["total"]


async def test_skills_breakdown_shape_and_counts(client: AsyncClient) -> None:
    headers = await login(client)

    body = (await client.get("/api/v1/progress/skills", headers=headers)).json()
    v = body["vocabulary"]
    assert v["learning"] == v["relearning"] == v["established"] == 0
    assert v["unseen"] > 0
    assert body["writing"]["unseen"] == 0  # nothing unlocked yet
    assert body["listening"]["total"] == 0
    assert body["reading"]["sentences_read"] == 0

    queue = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    await client.post(
        "/api/v1/reviews",
        json={"card_id": queue["cards"][0]["card_id"], "rating": 3},
        headers=headers,
    )
    body = (await client.get("/api/v1/progress/skills", headers=headers)).json()
    v = body["vocabulary"]
    assert v["learning"] + v["established"] == 1
