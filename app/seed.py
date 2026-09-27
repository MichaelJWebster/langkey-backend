"""Idempotent dev/content seeding and deck synchronisation.

Production startup runs Alembic migrations then `seed_data()`. Tests call
`create_schema_and_seed()`, which uses `create_all` instead of migrations.

Deck sync upserts items (matched by language + source text + translation) and
replaces their examples, so content edits propagate to an existing database
without touching user progress. A content hash in app_meta makes reruns no-ops.
"""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

import app.db as db
from app.auth import hash_password
from app.content import DeckEntry, deck_hash, load_deck
from app.db import Base
from app.models import AppMeta, Item, ItemExample, Language, User

DEV_USER_EMAIL = "dev@langkey.dev"
DEV_USER_PASSWORD = "langkey-dev"

LANGUAGES = {"nb": "Norwegian Bokmål"}


async def _sync_deck(session: AsyncSession, language: str, entries: list[DeckEntry]) -> None:
    fingerprint = deck_hash(entries)
    meta_key = f"deck_hash:{language}"
    meta = await session.get(AppMeta, meta_key)
    if meta is not None and meta.value == fingerprint:
        return

    existing = {
        (item.source_text, item.translation): item
        for item in await session.scalars(select(Item).where(Item.language_code == language))
    }
    for rank, e in enumerate(entries, start=1):
        examples = [
            ItemExample(position=pos, sentence=ex["no"], translation=ex["en"])
            for pos, ex in enumerate(e["examples"])
        ]
        item = existing.get((e["no"], e["en"]))
        if item is None:
            session.add(
                Item(
                    language_code=language,
                    item_type=e["type"],
                    source_text=e["no"],
                    translation=e["en"],
                    pos=e["pos"],
                    cefr_level=e["cefr"],
                    frequency_rank=rank,
                    topic=e.get("topic"),
                    examples=examples,
                )
            )
        else:
            item.item_type = e["type"]
            item.pos = e["pos"]
            item.cefr_level = e["cefr"]
            item.frequency_rank = rank
            item.topic = e.get("topic")
            item.examples = examples  # delete-orphan cascade drops the old rows

    if meta is None:
        session.add(AppMeta(key=meta_key, value=fingerprint))
    else:
        meta.value = fingerprint


async def seed_data() -> None:
    async with db.session_factory() as session:
        for code, name in LANGUAGES.items():
            if await session.get(Language, code) is None:
                session.add(Language(code=code, name=name))

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
        await session.commit()

        for code in LANGUAGES:
            entries, _source = await asyncio.to_thread(load_deck, code)
            await _sync_deck(session, code, entries)
        await session.commit()


async def create_schema_and_seed(engine: AsyncEngine) -> None:
    """Test-only: build the schema directly and seed. Production uses Alembic."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await seed_data()
