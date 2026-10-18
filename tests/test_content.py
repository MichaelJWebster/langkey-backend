from httpx import AsyncClient
from sqlalchemy import func, select

import app.db as db
from app.audio import DICTATION_SCALES, DICTATION_SUGGESTED
from app.content import merge_local_deck
from app.models import AppMeta, ExerciseAttempt, Item, ItemExample, User
from app.seed import seed_data
from tests.conftest import login


async def test_queue_cards_carry_multiple_examples(client: AsyncClient) -> None:
    headers = await login(client)
    body = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    first = body["cards"][0]["item"]
    assert first["source_text"] == "jeg"
    # First 150 items have the original example plus two extras.
    assert len(first["examples"]) == 3
    assert first["examples"][0]["sentence"] == "Jeg heter Anna."
    assert all("sentence" in ex and "translation" in ex for ex in first["examples"])


async def test_deck_sync_is_idempotent(client: AsyncClient) -> None:
    async with db.session_factory() as session:
        items_before = await session.scalar(select(func.count()).select_from(Item))
        examples_before = await session.scalar(select(func.count()).select_from(ItemExample))

    await seed_data()  # rerun the full seed + sync

    async with db.session_factory() as session:
        items_after = await session.scalar(select(func.count()).select_from(Item))
        examples_after = await session.scalar(select(func.count()).select_from(ItemExample))
    assert items_after == items_before
    assert examples_after == examples_before


async def test_local_deck_merge_shape() -> None:
    entries = merge_local_deck("nb")
    assert len(entries) == 860
    assert all(len(e["examples"]) >= 1 for e in entries)
    # Extras cover the first 150 items with two additional examples each.
    assert all(len(e["examples"]) == 3 for e in entries[:150])


async def test_categories_and_overall_progress(client: AsyncClient) -> None:
    headers = await login(client)
    body = (await client.get("/api/v1/categories", headers=headers)).json()
    by_slug = {c["category"]: c for c in body["categories"]}
    assert by_slug["travel"]["label"] == "Travel & getting around"
    assert by_slug["travel"]["remaining"] == by_slug["travel"]["total"]

    # Overlap: per-category totals sum above the deck size, because a word in
    # two categories is counted in both. The overall figure counts items once.
    assert sum(c["total"] for c in body["categories"]) > body["overall"]["total"]
    assert body["overall"]["total"] == body["overall"]["remaining"]


async def test_category_restricts_the_deck(client: AsyncClient) -> None:
    headers = await login(client)
    q = (await client.get("/api/v1/reviews/queue?category=travel", headers=headers)).json()
    assert q["new_count"] == 10
    assert all("travel" in c["item"]["categories"] for c in q["cards"])


async def test_category_beats_leftover_unseen_rows(client: AsyncClient) -> None:
    headers = await login(client)
    # Unfocused fetch creates 10 unseen rows from the front of the deck.
    await client.get("/api/v1/reviews/queue", headers=headers)
    # A category fetch must show only that category, not those leftovers.
    q = (await client.get("/api/v1/reviews/queue?category=food", headers=headers)).json()
    assert q["cards"], "expected a food deck"
    assert all("food" in c["item"]["categories"] for c in q["cards"])


async def test_glossary_records_card_and_example_words(client: AsyncClient) -> None:
    headers = await login(client)
    queue = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    card = queue["cards"][0]  # "jeg" — examples: "Jeg heter Anna." / "Jeg er sulten." / "Kan jeg sitte her?"
    await client.post(
        "/api/v1/reviews", json={"card_id": card["card_id"], "rating": 3}, headers=headers
    )
    entries = (await client.get("/api/v1/glossary", headers=headers)).json()
    by_word = {e["word"]: e for e in entries}
    assert by_word["jeg"]["via"] == "card"
    assert by_word["jeg"]["status"] == "established"
    # Words met inside the example sentences, before being taught:
    for w in ("å hete", "å være", "sulten", "å sitte", "her"):
        assert w in by_word, w
        assert by_word[w]["via"] == "example"
        assert by_word[w]["status"] == "seen"


async def test_listening_session_and_attempts(client: AsyncClient) -> None:
    headers = await login(client)
    # No cards studied yet: no exercises.
    empty = (await client.get("/api/v1/exercises/listening", headers=headers)).json()
    assert empty["exercises"] == []

    queue = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    for card in queue["cards"][:3]:
        await client.post(
            "/api/v1/reviews", json={"card_id": card["card_id"], "rating": 3}, headers=headers
        )

    body = (await client.get("/api/v1/exercises/listening?limit=8", headers=headers)).json()
    kinds = [e["kind"] for e in body["exercises"]]
    assert set(kinds) == {"dictation", "listen_choose"}
    # New learner (<25 attempts): word recognition dominates — 25% dictation.
    assert kinds.count("dictation") == 2
    for e in body["exercises"]:
        if e["kind"] == "dictation":
            assert e["target"]
            scales = [sp["scale"] for sp in e["speeds"]]
            assert scales == list(DICTATION_SCALES)
            # Slow until there's evidence -- but not the slowest: the two new
            # rungs are for the learner to choose, not imposed (ADR-046).
            assert e["suggested_scale"] == DICTATION_SUGGESTED[0] == 2.8
            assert all(sp["url"].startswith("/api/v1/audio/") for sp in e["speeds"])
            assert len({sp["url"] for sp in e["speeds"]}) == 5
        else:
            assert len(e["options"]) == 4
            assert 0 <= e["correct_index"] < 4
            assert e["options"][e["correct_index"]]

    resp = await client.post(
        "/api/v1/exercises/attempts",
        json={"exercise_type": "dictation", "example_id": 1, "item_id": 1,
              "correct": True, "near_miss": True, "hints": 2, "score": 0.67,
              "speed": 1.8, "answer": "jeg heter anna"},
        headers=headers,
    )
    assert resp.status_code == 201


async def test_deck_sync_keeps_example_ids_that_attempts_reference(
    client: AsyncClient,
) -> None:
    """A deck edit must not delete the example rows exercise attempts point at.

    Reassigning `item.examples` used to drop every row and insert fresh ones,
    which tripped `exercise_attempts_example_id_fkey` and took the whole
    startup down with it the first time the deck changed after any listening
    practice had been done.
    """
    async with db.session_factory() as session:
        item = await session.scalar(
            select(Item).where(Item.language_code == "nb").order_by(Item.frequency_rank)
        )
        example_id = item.examples[0].id
        user = await session.scalar(select(User))
        session.add(
            ExerciseAttempt(
                user_id=user.id,
                item_id=item.id,
                example_id=example_id,
                exercise_type="dictation",
                correct=True,
            )
        )
        await session.commit()

    # Force a resync by clearing the content fingerprint.
    async with db.session_factory() as session:
        meta = await session.get(AppMeta, "deck_hash:nb")
        if meta is not None:
            await session.delete(meta)
            await session.commit()

    await seed_data()  # must not raise

    async with db.session_factory() as session:
        attempt = await session.scalar(select(ExerciseAttempt))
        assert attempt.example_id == example_id
        assert await session.get(ItemExample, example_id) is not None
