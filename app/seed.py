"""Idempotent dev/content seeding and deck synchronisation.

Production startup runs Alembic migrations then `seed_data()`. Tests call
`create_schema_and_seed()`, which uses `create_all` instead of migrations.

Deck sync upserts items (matched by language + source text + translation) and
updates their examples in place, so content edits propagate to an existing
database without touching user progress. A content hash in app_meta makes
reruns no-ops. Example rows are reused rather than replaced: exercise
attempts reference them by id.
"""

import asyncio

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

import app.db as db
from app.auth import hash_password
from app.config import get_settings
from app.content import DeckEntry, deck_hash, load_deck
from app.db import Base
from app.models import (
    AppMeta,
    ExerciseAttempt,
    Item,
    ItemCategory,
    ItemExample,
    Language,
    User,
)

DEV_USER_EMAIL = "dev@langkey.dev"
DEV_USER_PASSWORD = "langkey-dev"

LANGUAGES = {"nb": "Norwegian Bokmål"}


def _sync_examples(item: Item, examples: list[dict]) -> list[ItemExample]:
    """Bring an item's example rows in line with the deck, reusing the rows.

    Reassigning `item.examples` would be shorter, but it deletes every row and
    inserts fresh ones with new ids -- and `exercise_attempts.example_id`
    points at those ids, so a deck edit raised a foreign-key violation and
    took the whole startup down with it. Updating in place keeps the ids, so a
    listening attempt still knows which sentence it was about.

    Returns the surplus rows, which the caller drops once nothing refers to
    them any more.
    """
    for pos, ex in enumerate(examples):
        if pos < len(item.examples):
            row = item.examples[pos]
            row.position = pos
            row.sentence = ex["no"]
            row.translation = ex["en"]
        else:
            item.examples.append(
                ItemExample(position=pos, sentence=ex["no"], translation=ex["en"])
            )
    surplus = item.examples[len(examples):]
    del item.examples[len(examples):]
    return surplus


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
    dropped: list[ItemExample] = []
    for rank, e in enumerate(entries, start=1):
        categories = [ItemCategory(category=c) for c in e.get("categories") or ()]
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
                    examples=[
                        ItemExample(position=pos, sentence=ex["no"], translation=ex["en"])
                        for pos, ex in enumerate(e["examples"])
                    ],
                    categories=categories,
                )
            )
        else:
            item.item_type = e["type"]
            item.pos = e["pos"]
            item.cefr_level = e["cefr"]
            item.frequency_rank = rank
            item.categories = categories
            dropped += _sync_examples(item, e["examples"])

    if dropped:
        # An item lost an example. Release the attempts that point at the rows
        # about to go: the column is nullable for exactly this, and losing
        # which sentence an old attempt used beats losing the attempt.
        await session.flush()
        ids = [row.id for row in dropped if row.id is not None]
        if ids:
            await session.execute(
                update(ExerciseAttempt)
                .where(ExerciseAttempt.example_id.in_(ids))
                .values(example_id=None)
            )

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

        # The well-known dev login must never exist outside dev; elsewhere the
        # initial user comes from LANGKEY_ADMIN_EMAIL / LANGKEY_ADMIN_PASSWORD.
        settings = get_settings()
        if settings.environment == "dev":
            email, password, name = DEV_USER_EMAIL, DEV_USER_PASSWORD, "Dev Learner"
        else:
            email, password, name = settings.admin_email, settings.admin_password, "Learner"
        if email and password:
            if await session.scalar(select(User).where(User.email == email)) is None:
                session.add(
                    User(email=email, display_name=name, password_hash=hash_password(password))
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
