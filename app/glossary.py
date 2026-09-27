"""Glossary population: record which deck words a user has encountered.

A card review records the card's own item ("card") and any other deck items
recognised inside its example sentences ("example"), so words met in context
enter the glossary before they are formally taught.

Matching is heuristic morphology, not a parser: each single-word lemma expands
to common Norwegian inflections (plus a table of irregular verb forms), and
multi-word items match by substring. Misses are acceptable; the glossary is a
reference, not the scheduler.
"""

import re
import string
import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Item, ReviewEvent, UserCard, UserGlossaryEntry

IRREGULAR_FORMS = {
    "er": "å være", "var": "å være",
    "kan": "å kunne", "kunne": "å kunne",
    "vil": "å ville", "ville": "å ville",
    "skal": "å skulle", "skulle": "å skulle",
    "må": "å måtte", "måtte": "å måtte",
    "gjør": "å gjøre", "gjorde": "å gjøre",
    "spør": "å spørre", "spurte": "å spørre",
    "vet": "å vite", "visste": "å vite",
    "ble": "å bli", "kom": "å komme", "fikk": "å få",
    "gikk": "å gå", "tok": "å ta", "sto": "å stå", "sov": "å sove",
    "sa": "å si", "så": None,  # 'så' is usually the adverb — never match
}

_WORD_RE = re.compile(r"[a-zæøåéA-ZÆØÅÉ]+")


def _lemma_forms(lemma: str) -> set[str]:
    """Common inflected forms for a single-word lemma."""
    forms = {lemma}
    for suffix in ("r", "er", "en", "et", "a", "e", "t", "ene", "ne"):
        forms.add(lemma + suffix)
    if lemma.endswith("e"):
        stem = lemma[:-1]
        forms.update({stem + "a", stem + "en", stem + "er", stem + "et"})
    return forms


class FormIndex:
    """Maps surface forms (and phrase substrings) to item ids for one language."""

    def __init__(self, items: list[Item]):
        by_source: dict[str, int] = {}
        self.forms: dict[str, int] = {}
        self.phrases: list[tuple[str, int]] = []
        for item in items:
            by_source[item.source_text.lower()] = item.id
            bare = item.source_text.lower()
            for prefix in ("å ", "en ", "ei ", "et "):
                if bare.startswith(prefix):
                    bare = bare[len(prefix):]
                    break
            if " " in bare:
                self.phrases.append((bare.strip(string.punctuation + "…?"), item.id))
                continue
            for form in _lemma_forms(bare):
                self.forms.setdefault(form, item.id)
        for form, lemma in IRREGULAR_FORMS.items():
            if lemma is None:
                self.forms.pop(form, None)
            elif lemma.lower() in by_source:
                self.forms[form] = by_source[lemma.lower()]

    def match(self, sentence: str) -> set[int]:
        lowered = sentence.lower()
        found = {
            self.forms[token]
            for token in _WORD_RE.findall(lowered)
            if token in self.forms
        }
        found.update(item_id for phrase, item_id in self.phrases if phrase in lowered)
        return found


_index_cache: dict[tuple[str, int], FormIndex] = {}


async def _form_index(session: AsyncSession, language: str) -> FormIndex:
    count = await session.scalar(
        select(func.count()).select_from(Item).where(Item.language_code == language)
    )
    key = (language, count or 0)
    if key not in _index_cache:
        _index_cache.clear()  # deck changed; drop stale indexes
        items = list(
            await session.scalars(select(Item).where(Item.language_code == language))
        )
        _index_cache[key] = FormIndex(items)
    return _index_cache[key]


async def record_encounters(
    session: AsyncSession, user_id: uuid.UUID, item: Item, seen_at: datetime
) -> None:
    """Upsert glossary entries for a card's item and its example-sentence words."""
    index = await _form_index(session, item.language_code)
    linked: set[int] = set()
    for example in item.examples:
        linked |= index.match(example.sentence)
    linked.discard(item.id)

    for item_id, via in [(item.id, "card"), *((i, "example") for i in sorted(linked))]:
        existing = await session.get(UserGlossaryEntry, (user_id, item_id))
        if existing is None:
            session.add(
                UserGlossaryEntry(
                    user_id=user_id, item_id=item_id, first_seen_at=seen_at, via=via
                )
            )


async def backfill_glossary() -> None:
    """One-off: build glossary entries from review history predating the feature."""
    import app.db as db

    async with db.session_factory() as session:
        rows = await session.execute(
            select(UserCard, func.min(ReviewEvent.reviewed_at))
            .join(ReviewEvent, ReviewEvent.user_card_id == UserCard.id)
            .group_by(UserCard.id)
        )
        for card, first_seen in rows:
            await record_encounters(session, card.user_id, card.item, first_seen)
        await session.commit()
