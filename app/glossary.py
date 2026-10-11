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
        #: Every item a surface form could mean, not just the first claimant.
        #: `_lemma_forms` generates inflections blindly, so forms collide --
        #: "dusjer" is both `en dusj` + "er" and the present tense of
        #: `å dusje`. `forms` keeps one answer because the user glossary only
        #: needs to know the word was met; anything that *shows* a translation
        #: needs all of them, or it will confidently gloss a verb as a noun.
        self.form_items: dict[str, list[int]] = {}
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
                ids = self.form_items.setdefault(form, [])
                if item.id not in ids:
                    ids.append(item.id)
        for form, lemma in IRREGULAR_FORMS.items():
            if lemma is None:
                self.forms.pop(form, None)
                self.form_items.pop(form, None)
            elif lemma.lower() in by_source:
                # An irregular form is known exactly, so it overrides the
                # guesses rather than joining them.
                self.forms[form] = by_source[lemma.lower()]
                self.form_items[form] = [by_source[lemma.lower()]]

    def match(self, sentence: str) -> set[int]:
        lowered = sentence.lower()
        found = {
            self.forms[token]
            for token in _WORD_RE.findall(lowered)
            if token in self.forms
        }
        found.update(item_id for phrase, item_id in self.phrases if phrase in lowered)
        return found


def matched_forms(index: FormIndex, sentence: str) -> list[tuple[str, int, int]]:
    """Deck items recognised in `sentence`, with the form that matched them.

    `FormIndex.match` answers "which items are in here", which is all the
    glossary needs. A reading passage needs more: to gloss an unseen word it
    has to show the word *as written* -- "bøker", not "ei bok" -- so the
    matching token itself has to come back out.

    Returns (surface form, item id, character offset), one entry per item, at
    its first occurrence, ordered by position in the sentence. Phrases are
    matched by substring as they are in `match`, and the slice of the original
    text is returned so the original capitalisation survives.
    """
    lowered = sentence.lower()
    found: dict[int, tuple[str, int]] = {}
    for m in _WORD_RE.finditer(sentence):
        item_id = index.forms.get(m.group().lower())
        if item_id is not None and item_id not in found:
            found[item_id] = (m.group(), m.start())
    for phrase, item_id in index.phrases:
        at = lowered.find(phrase)
        if at >= 0 and item_id not in found:
            found[item_id] = (sentence[at : at + len(phrase)], at)
    return sorted(
        ((form, item_id, at) for item_id, (form, at) in found.items()),
        key=lambda row: row[2],
    )


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


async def form_index(session: AsyncSession, language: str) -> FormIndex:
    """The cached FormIndex for a language, for callers outside this module."""
    return await _form_index(session, language)


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
