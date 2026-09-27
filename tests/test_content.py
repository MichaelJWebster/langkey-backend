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
    assert len(entries) == 447
    assert all(len(e["examples"]) >= 1 for e in entries)
    # Extras cover the first 150 items with two additional examples each.
    assert all(len(e["examples"]) == 3 for e in entries[:150])
