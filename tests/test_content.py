from httpx import AsyncClient
from sqlalchemy import func, select

import app.db as db
from app.content import merge_local_deck
from app.models import Item, ItemExample
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
    assert len(entries) == 471
    assert all(len(e["examples"]) >= 1 for e in entries)
    # Extras cover the first 150 items with two additional examples each.
    assert all(len(e["examples"]) == 3 for e in entries[:150])


async def test_topics_and_focus(client: AsyncClient) -> None:
    headers = await login(client)
    topics = (await client.get("/api/v1/topics", headers=headers)).json()
    by_slug = {t["topic"]: t for t in topics}
    assert by_slug["shopping"]["label"] == "Buying things"
    assert by_slug["directions"]["remaining"] == by_slug["directions"]["total"] == 20

    q = (await client.get("/api/v1/reviews/queue?focus=directions", headers=headers)).json()
    assert q["new_count"] == 10
    first = q["cards"][0]["item"]
    assert first["source_text"] == "til venstre"  # lowest-rank directions item


async def test_focus_beats_leftover_unseen_rows(client: AsyncClient) -> None:
    headers = await login(client)
    # Unfocused fetch creates 10 unseen basics rows.
    await client.get("/api/v1/reviews/queue", headers=headers)
    # A focused fetch must still lead with the focus topic, not those leftovers.
    q = (await client.get("/api/v1/reviews/queue?focus=shopping", headers=headers)).json()
    firsts = [c["item"]["source_text"] for c in q["cards"][:3]]
    assert firsts[0] == "å handle", firsts


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
