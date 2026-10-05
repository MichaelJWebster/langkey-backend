"""Import a prepared text (staging JSON) into Postgres.

Reads a file produced by scripts/prepare_text.py and inserts the text, its
sections, sentences, and current translations. Refuses to overwrite an
existing slug unless --replace is given (which cascades away the old rows,
including reader positions on that text).

Usage:
    uv run python -m scripts.import_text data/texts_staging/askeladden-trollet.json
    uv run python -m scripts.import_text data/texts_staging/x.json --replace
"""

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import delete, select

from app.db import session_factory
from scripts.translation_gates import check_all
from app.models import (
    Language,
    ReadingText,
    Sentence,
    SentenceTranslation,
    TextSection,
)


def check_quality(path: Path, sentences: list[dict], force: bool) -> None:
    """Refuse an import the gates say is fixable.

    Translation-side findings have an obvious next step, so they block:
    check_translation --repair clears most of them. Source-side findings mean
    the Norwegian itself is wrong and the fix is upstream in extraction, and
    those checks have known false positives, so they warn rather than block --
    a gate that forces --force every time teaches you to ignore it.
    """
    findings = check_all(sentences)
    fixable = [f for f in findings if f.repairable]
    other = [f for f in findings if not f.repairable]

    if other:
        counts = Counter(f.gate for f in other)
        print(f"NOTE: {len(other)} source-side findings "
              f"({', '.join(f'{n} {g}' for g, n in counts.most_common())})")
        print("  These need a human; see check_translation --show <gate>.")

    if not fixable:
        return
    counts = Counter(f.gate for f in fixable)
    s = "" if len(fixable) == 1 else "s"
    print(f"{len(fixable)} fixable finding{s}: "
          f"{', '.join(f'{n} {g}' for g, n in counts.most_common())}")
    for f in fixable[:5]:
        print(f"  #{f.seq} {f.detail}")
        print(f"    {f.en[:90]}")
    if force:
        print("--force given: importing anyway.")
        return
    sys.exit(
        "Refusing to import. Either fix them:\n"
        f"  uv run python -m scripts.check_translation {path} --repair\n"
        "or import as-is with --force."
    )


async def import_text(path: Path, replace: bool, force: bool = False) -> None:
    data = json.loads(path.read_text())
    check_quality(path, data["sentences"], force)

    async with session_factory() as session:
        if not await session.get(Language, data["language"]):
            sys.exit(f"Language {data['language']!r} not seeded")
        existing = await session.scalar(
            select(ReadingText).where(ReadingText.slug == data["slug"])
        )
        if existing and not replace:
            sys.exit(f"Text {data['slug']!r} already imported (id {existing.id}); use --replace")
        # A hand-corrected English title lives only in the database -- the
        # staging file has whatever the model proposed, or nothing at all for
        # a text imported before titles were translated. Re-importing must not
        # throw that away, so the staging file only wins when it has one.
        kept_title_en = existing.title_en if existing else None
        if existing:
            await session.execute(delete(ReadingText).where(ReadingText.id == existing.id))

        text = ReadingText(
            slug=data["slug"],
            title=data["title"],
            title_en=data.get("title_en") or kept_title_en,
            author=data.get("author"),
            language_code=data["language"],
            kind=data["kind"],
            orthography=data.get("orthography", "nb"),
            source_url=data.get("source_url"),
            license=data.get("license"),
        )
        session.add(text)
        await session.flush()

        section_ids: dict[int, int] = {}
        for sec in data["sections"]:
            row = TextSection(text_id=text.id, seq=sec["seq"], heading=sec.get("heading"))
            session.add(row)
            await session.flush()
            section_ids[sec["seq"]] = row.id

        rows = [
            Sentence(
                text_id=text.id,
                section_id=section_ids.get(s.get("section")),
                seq=s["seq"],
                paragraph=s["paragraph"],
                no_text=s["no"],
            )
            for s in data["sentences"]
        ]
        session.add_all(rows)
        await session.flush()  # one round-trip for all sentence ids
        session.add_all(
            SentenceTranslation(
                sentence_id=row.id,
                translator=data.get("translator", "unknown"),
                en_text=s["en"],
            )
            for row, s in zip(rows, data["sentences"])
        )
        await session.commit()
        print(f"Imported {data['slug']!r}: {len(data['sentences'])} sentences (text id {text.id})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", type=Path)
    ap.add_argument("--replace", action="store_true")
    ap.add_argument(
        "--force", action="store_true", help="import even though the gates found faults"
    )
    args = ap.parse_args()
    asyncio.run(import_text(args.file, args.replace, args.force))


if __name__ == "__main__":
    main()
