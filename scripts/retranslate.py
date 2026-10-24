"""Re-translate library texts already in the database with a newer model.

ADR-021 versions translations per translator, with one current per sentence,
so a better model can replace a draft in place without re-importing the text
or touching anyone's reading position. This runs the same translation path as
scripts/prepare_text.py -- a paragraph at a time for context, the echoed-
Norwegian retry -- and writes each new translation as the current one.

A new translation replaces the old only if it passes the translation-side
gates (scripts/translation_gates.py; source-side findings are faults in the
Norwegian that no translation can fix). Otherwise the old stays current and
the sentence is listed. Each paragraph commits on its own, so an interrupted
run loses nothing and re-running skips what is already done.

    uv run python -m scripts.retranslate --text mysterier --dry-run --limit 20
    caffeinate -i uv run python -m scripts.retranslate --all
    uv run python -m scripts.retranslate --text kveita --restore gemma3:12b

`--restore` makes another translator's latest version current again, for a
text or --all. Nothing is ever deleted.

Run it under `caffeinate -i`: this Mac idle-sleeps after a minute.
"""

import argparse
import asyncio
import sys
import time

from sqlalchemy import select, update

from app.db import session_factory
from app.models import ReadingText, Sentence, SentenceTranslation
from scripts.prepare_text import (
    DEFAULT_MODEL,
    looks_untranslated,
    ollama,
    translate_paragraph,
)
from scripts.translation_gates import check_sentence


def retry_echo(model: str, no: str, en: str) -> str:
    """prepare_text's retry: a reply that echoes the Norwegian is asked again, alone."""
    for _ in range(2):
        if not looks_untranslated(no, en):
            break
        en = ollama(model, "Translate this Norwegian sentence into English. Reply with only the English translation.\n\n" + no)
    return en


async def texts(slugs: list[str] | None) -> list[ReadingText]:
    async with session_factory() as session:
        query = select(ReadingText).order_by(ReadingText.id)
        if slugs:
            query = query.where(ReadingText.slug.in_(slugs))
        found = list(await session.scalars(query))
    missing = set(slugs or ()) - {t.slug for t in found}
    if missing:
        sys.exit(f"no such text: {', '.join(sorted(missing))}")
    return found


async def paragraphs(text_id: int, model: str):
    """[(paragraph, [(sentence, current translation)])] still to do, in order."""
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(Sentence, SentenceTranslation)
                .join(SentenceTranslation, SentenceTranslation.sentence_id == Sentence.id)
                .where(Sentence.text_id == text_id, SentenceTranslation.is_current.is_(True))
                .order_by(Sentence.seq)
            )
        ).all()
    grouped: dict[int, list] = {}
    for sentence, current in rows:
        grouped.setdefault(sentence.paragraph, []).append((sentence, current))
    # A paragraph is done when every sentence in it is either on this model
    # or was kept on purpose (gate failure) -- the latter is recorded by a
    # non-current version from this model.
    return [(p, pairs) for p, pairs in grouped.items() if any(c.translator != model for _, c in pairs)]


async def tried(sentence_ids: list[int], model: str) -> set[int]:
    async with session_factory() as session:
        return set(
            await session.scalars(
                select(SentenceTranslation.sentence_id).where(
                    SentenceTranslation.sentence_id.in_(sentence_ids), SentenceTranslation.translator == model
                )
            )
        )


async def write(pairs, new: list[str], model: str, dry_run: bool) -> tuple[int, list[tuple]]:
    """Make each passing translation current; keep the old where it fails."""
    replaced, kept = 0, []
    async with session_factory() as session:
        for (sentence, current), en in zip(pairs, new):
            blocked = [f for f in check_sentence(sentence.seq, sentence.no_text, en) if f.repairable]
            make_current = not blocked
            if blocked:
                kept.append((sentence.seq, sentence.no_text, en, blocked[0].gate))
            else:
                replaced += 1
            if dry_run:
                continue
            if make_current:
                await session.execute(
                    update(SentenceTranslation)
                    .where(SentenceTranslation.sentence_id == sentence.id, SentenceTranslation.is_current.is_(True))
                    .values(is_current=False)
                )
            # Kept failures are stored too (not current), so a re-run knows
            # this sentence was tried and does not ask again.
            session.add(SentenceTranslation(sentence_id=sentence.id, translator=model, en_text=en, is_current=make_current))
        if not dry_run:
            await session.commit()
    return replaced, kept


async def restore(slugs: list[str] | None, translator: str) -> None:
    for text in await texts(slugs):
        async with session_factory() as session:
            sentence_ids = list(await session.scalars(select(Sentence.id).where(Sentence.text_id == text.id)))
            switched = 0
            for sid in sentence_ids:
                latest = await session.scalar(
                    select(SentenceTranslation)
                    .where(SentenceTranslation.sentence_id == sid, SentenceTranslation.translator == translator)
                    .order_by(SentenceTranslation.id.desc())
                    .limit(1)
                )
                if latest is None or latest.is_current:
                    continue
                await session.execute(
                    update(SentenceTranslation)
                    .where(SentenceTranslation.sentence_id == sid, SentenceTranslation.is_current.is_(True))
                    .values(is_current=False)
                )
                latest.is_current = True
                switched += 1
            await session.commit()
        print(f"{text.slug}: {switched} sentences back on {translator}")


async def run(slugs: list[str] | None, model: str, dry_run: bool, limit: int | None) -> None:
    started, total_replaced, total_kept = time.time(), 0, []
    for text in await texts(slugs):
        todo = await paragraphs(text.id, model)
        if todo and not dry_run:
            # Skip sentences this model already tried and the gates kept back.
            done_before = await tried([s.id for _, pairs in todo for s, _ in pairs], model)
            todo = [(p, [(s, c) for s, c in pairs if s.id not in done_before]) for p, pairs in todo]
            todo = [(p, pairs) for p, pairs in todo if pairs]
        n = sum(len(pairs) for _, pairs in todo)
        if limit:
            kept_n, trimmed = 0, []
            for p, pairs in todo:
                if kept_n >= limit:
                    break
                trimmed.append((p, pairs))
                kept_n += len(pairs)
            todo, n = trimmed, kept_n
        if not todo:
            print(f"{text.slug}: nothing to do")
            continue
        t0, done = time.time(), 0
        for _, pairs in todo:
            new = translate_paragraph(model, [s.no_text for s, _ in pairs])
            new = [retry_echo(model, s.no_text, en) for (s, _), en in zip(pairs, new)]
            replaced, kept = await write(pairs, new, model, dry_run)
            total_replaced += replaced
            total_kept += [(text.slug, *k) for k in kept]
            done += len(pairs)
            rate = done / max(time.time() - t0, 1e-6)
            print(f"\r  {text.slug}: {done}/{n} (~{(n - done) / rate / 60:.0f} min left) ", end="", flush=True)
            if dry_run and done <= 12:
                print()
                for (s, c), en in zip(pairs, new):
                    print(f"    NO  {s.no_text}\n    OLD {c.en_text}\n    NEW {en}")
        print()
    print(f"\n{'would replace' if dry_run else 'replaced'} {total_replaced} translations"
          f" in {(time.time() - started) / 60:.1f} min; kept the old for {len(total_kept)}")
    for slug, seq, no, en, gate in total_kept[:20]:
        print(f"  {slug} #{seq} [{gate}] {no[:60]!r} -> {en[:60]!r}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--text", action="append", help="text slug (repeatable)")
    which.add_argument("--all", action="store_true")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--dry-run", action="store_true", help="translate and report, write nothing")
    ap.add_argument("--limit", type=int, help="stop after about this many sentences per text")
    ap.add_argument("--restore", metavar="TRANSLATOR", help="make this translator's version current again")
    args = ap.parse_args()
    slugs = None if args.all else args.text
    if args.restore:
        asyncio.run(restore(slugs, args.restore))
    else:
        asyncio.run(run(slugs, args.model, args.dry_run, args.limit))


if __name__ == "__main__":
    main()
