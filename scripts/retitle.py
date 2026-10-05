"""Set or correct the English title of an imported text.

Titles are translated at prepare time and gate-checked, but a title is the one
string a learner reads before choosing a text and a machine translation of
four words has little to go on. This fixes one without re-importing, and
backfills texts that were imported before the column existed.

Usage:
    uv run python -m scripts.retitle --list
    uv run python -m scripts.retitle mysterier "Mysteries"
    uv run python -m scripts.retitle mysterier --clear
    uv run python -m scripts.retitle --translate-missing [--dry-run]

--translate-missing fills in texts that have no English title, which is what
every text imported before the column existed looks like. Read what it
proposes: a title is four words with no context, and the model gets a fair
number of them wrong in ways no gate can see.
"""

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.db import session_factory
from app.models import ReadingText


async def show() -> None:
    async with session_factory() as session:
        rows = await session.scalars(select(ReadingText).order_by(ReadingText.title))
        for t in rows:
            print(f"{t.slug:44} {t.title!r}")
            print(f"{'':44} en: {t.title_en!r}")


async def translate_missing(dry_run: bool) -> None:
    from scripts.prepare_text import DEFAULT_MODEL, translate_title

    async with session_factory() as session:
        rows = list(
            await session.scalars(
                select(ReadingText)
                .where(ReadingText.title_en.is_(None))
                .order_by(ReadingText.title)
            )
        )
        if not rows:
            print("Every text already has an English title.")
            return
        print(f"{len(rows)} without an English title.\n")
        kept = dropped = 0
        for t in rows:
            proposed = translate_title(DEFAULT_MODEL, t.title)
            if proposed:
                kept += 1
                print(f"  {t.title}\n    -> {proposed}")
                if not dry_run:
                    t.title_en = proposed
            else:
                dropped += 1
                print(f"  {t.title}\n    -> (nothing usable; set it by hand)")
        if dry_run:
            print(f"\nDry run: {kept} would be set, {dropped} left for you.")
            return
        await session.commit()
        print(f"\nSet {kept}, left {dropped} for you.")
        print("Check them with --list and correct any with:")
        print("  uv run python -m scripts.retitle <slug> \"<better title>\"")


async def apply(slug: str, title_en: str | None) -> None:
    async with session_factory() as session:
        text = await session.scalar(select(ReadingText).where(ReadingText.slug == slug))
        if not text:
            sys.exit(f"No text with slug {slug!r}. Try --list.")
        before = text.title_en
        text.title_en = title_en
        await session.commit()
        print(f"{slug}: {before!r} -> {title_en!r}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("slug", nargs="?")
    ap.add_argument("title_en", nargs="?")
    ap.add_argument("--list", action="store_true", help="show every text and its English title")
    ap.add_argument("--clear", action="store_true", help="remove the English title")
    ap.add_argument(
        "--translate-missing",
        action="store_true",
        help="translate the title of every text that has no English one",
    )
    ap.add_argument("--dry-run", action="store_true", help="with --translate-missing")
    args = ap.parse_args()

    if args.list:
        asyncio.run(show())
        return
    if args.translate_missing:
        asyncio.run(translate_missing(args.dry_run))
        return
    if not args.slug:
        ap.error("give a slug, or --list")
    if args.clear:
        asyncio.run(apply(args.slug, None))
        return
    if not args.title_en:
        ap.error("give the English title, or --clear")
    asyncio.run(apply(args.slug, args.title_en))


if __name__ == "__main__":
    main()
