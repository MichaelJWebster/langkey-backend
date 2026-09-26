"""Idempotent dev/content seeding.

Production startup runs Alembic migrations then `seed_data()`. Tests call
`create_schema_and_seed()`, which uses `create_all` instead of migrations.
"""

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

import app.db as db
from app.auth import hash_password
from app.db import Base
from app.models import AppMeta, Item, Language, User

DEV_USER_EMAIL = "dev@langkey.dev"
DEV_USER_PASSWORD = "langkey-dev"

SEED_DECK = Path(__file__).parent.parent / "data" / "seed_deck_nb.json"


async def seed_data() -> None:
    async with db.session_factory() as session:
        if await session.get(Language, "nb") is None:
            session.add(Language(code="nb", name="Norwegian Bokmål"))

        if await session.get(AppMeta, "greeting") is None:
            session.add(AppMeta(key="greeting", value="Hei! Velkommen til LangKey 🇳🇴"))

        if await session.scalar(select(User).where(User.email == DEV_USER_EMAIL)) is None:
            session.add(
                User(
                    email=DEV_USER_EMAIL,
                    display_name="Dev Learner",
                    password_hash=hash_password(DEV_USER_PASSWORD),
                )
            )

        item_count = await session.scalar(
            select(func.count()).select_from(Item).where(Item.language_code == "nb")
        )
        if not item_count and SEED_DECK.exists():
            entries = json.loads(SEED_DECK.read_text())
            for rank, e in enumerate(entries, start=1):
                session.add(
                    Item(
                        language_code="nb",
                        item_type=e.get("type", "word"),
                        source_text=e["no"],
                        translation=e["en"],
                        pos=e.get("pos"),
                        cefr_level=e.get("cefr", "A1"),
                        frequency_rank=rank,
                        example_sentence=e["ex_no"],
                        example_translation=e["ex_en"],
                    )
                )

        await session.commit()


async def create_schema_and_seed(engine: AsyncEngine) -> None:
    """Test-only: build the schema directly and seed. Production uses Alembic."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await seed_data()
