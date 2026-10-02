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


async def test_example_gloss_excludes_headword_and_established(client: AsyncClient) -> None:
    import uuid as uuid_mod

    from sqlalchemy import select

    import app.db as db
    from app.models import FSRS_REVIEW_STATE, RECOGNITION, Item, User, UserCard

    headers = await login(client)
    queue = (await client.get("/api/v1/reviews/queue", headers=headers)).json()

    glossed = [
        (card, ex)
        for card in queue["cards"]
        for ex in card["item"]["examples"]
        if ex["gloss"]
    ]
    assert glossed, "expected at least one example with gloss entries"
    card, ex = glossed[0]
    words = [g["word"] for g in ex["gloss"]]
    assert card["item"]["source_text"] not in words  # headword never glossed
    assert all(g["translation"] for g in ex["gloss"])

    # Establish one glossed word's recognition card: it must drop out.
    target_word = words[0]
    async with db.session_factory() as session:
        user_id = (await session.scalars(select(User.id))).first()
        item_id = (
            await session.scalars(select(Item.id).where(Item.source_text == target_word))
        ).first()
        existing = (
            await session.scalars(
                select(UserCard).where(
                    UserCard.user_id == user_id,
                    UserCard.item_id == item_id,
                    UserCard.direction == RECOGNITION,
                )
            )
        ).first()
        if existing is None:
            existing = UserCard(
                id=uuid_mod.uuid4(), user_id=user_id, item_id=item_id, direction=RECOGNITION
            )
            session.add(existing)
        existing.fsrs_state = FSRS_REVIEW_STATE
        await session.commit()

    queue = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    for c in queue["cards"]:
        for e in c["item"]["examples"]:
            assert target_word not in [g["word"] for g in e["gloss"]]


async def test_answer_alternatives_round_trip(client: AsyncClient) -> None:
    headers = await login(client)
    queue = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    card = queue["cards"][0]
    assert card["alternatives"] == []

    resp = await client.post(
        "/api/v1/reviews/alternatives",
        json={"card_id": card["card_id"], "answer": "  my own wording  "},
        headers=headers,
    )
    assert resp.status_code == 201

    # Recording the same answer twice is harmless.
    resp = await client.post(
        "/api/v1/reviews/alternatives",
        json={"card_id": card["card_id"], "answer": "my own wording"},
        headers=headers,
    )
    assert resp.status_code == 201

    queue = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    same = next(c for c in queue["cards"] if c["card_id"] == card["card_id"])
    assert same["alternatives"] == ["my own wording"]


async def test_alternative_rejects_another_users_card(client: AsyncClient) -> None:
    headers = await login(client)
    resp = await client.post(
        "/api/v1/reviews/alternatives",
        json={"card_id": "00000000-0000-0000-0000-000000000000", "answer": "x"},
        headers=headers,
    )
    assert resp.status_code == 404
