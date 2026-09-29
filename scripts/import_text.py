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
from pathlib import Path

from sqlalchemy import delete, select

from app.db import session_factory
from app.models import (
    Language,
    ReadingText,
    Sentence,
    SentenceTranslation,
    TextSection,
)


async def import_text(path: Path, replace: bool) -> None:
    data = json.loads(path.read_text())
    missing = [s["seq"] for s in data["sentences"] if not s.get("en")]
    if missing:
        sys.exit(f"Refusing to import: {len(missing)} sentences lack translations, e.g. seq {missing[:5]}")

    async with session_factory() as session:
        if not await session.get(Language, data["language"]):
            sys.exit(f"Language {data['language']!r} not seeded")
        existing = await session.scalar(
            select(ReadingText).where(ReadingText.slug == data["slug"])
        )
        if existing and not replace:
            sys.exit(f"Text {data['slug']!r} already imported (id {existing.id}); use --replace")
        if existing:
            await session.execute(delete(ReadingText).where(ReadingText.id == existing.id))

        text = ReadingText(
            slug=data["slug"],
            title=data["title"],
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
    args = ap.parse_args()
    asyncio.run(import_text(args.file, args.replace))


if __name__ == "__main__":
    main()
