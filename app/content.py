"""Deck content pipeline.

Source of truth while authoring lives in-repo: `data/seed_deck_nb.json` plus
`data/extra_examples_nb.json` (additional example sentences, keyed by
"<norwegian>||<english>"). `scripts/publish_deck.py` merges these and publishes
a Parquet snapshot to MinIO/S3; at startup the API prefers that snapshot and
falls back to the local files (e.g. in tests, or before the first publish).

Postgres remains the operational store — this module only feeds it.
"""

import hashlib
import json
import logging
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"

DeckEntry = dict[str, Any]
# keys: no, en, pos, cefr, type, categories[str], examples[{no,en}]


def merge_local_deck(language: str = "nb") -> list[DeckEntry]:
    """Build full deck entries from the local source files."""
    deck = json.loads((DATA_DIR / f"seed_deck_{language}.json").read_text())
    extras_path = DATA_DIR / f"extra_examples_{language}.json"
    extras = json.loads(extras_path.read_text()) if extras_path.exists() else {}

    entries: list[DeckEntry] = []
    for e in deck:
        examples = [{"no": e["ex_no"], "en": e["ex_en"]}]
        for pair in extras.get(f"{e['no']}||{e['en']}", []):
            examples.append({"no": pair[0], "en": pair[1]})
        entries.append(
            {
                "no": e["no"],
                "en": e["en"],
                "pos": e.get("pos"),
                "cefr": e.get("cefr", "A1"),
                "type": e.get("type", "word"),
                "categories": sorted(set(e.get("categories") or ())),
                "examples": examples,
            }
        )
    return entries


PassageEntry = dict[str, Any]
# keys: slug, kind, topics[str], no, en, lines[{role,no,en}] | None,
#       glossary[{form,lemma,en}]


def load_passages(language: str = "nb") -> list[PassageEntry]:
    """A1 translation passages from the local JSONL, in authored order.

    One JSON object per line, hand-written and reviewed rather than generated
    at request time (ADR-041). Local-only for now: 25 short passages are not
    worth a Parquet snapshot, and the container image carries `data/`.
    """
    path = DATA_DIR / f"a1_passages_{language}.jsonl"
    if not path.exists():
        return []
    entries: list[PassageEntry] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        lines = [
            {"role": ln.get("role", "line"), "no": ln["no"], "en": ln["en"]}
            for ln in e.get("lines") or ()
        ] or None
        # When a passage is authored as lines, the whole text is derived from
        # them rather than written twice. Two copies of the same sentence drift
        # apart, and the gates run on the derived text, so a drift would mean
        # gating something the learner never sees.
        entries.append(
            {
                "slug": e["slug"],
                "kind": e.get("kind", "sentence"),
                "topics": sorted(set(e.get("topics") or ())),
                "no": " ".join(ln["no"] for ln in lines) if lines else e["no"],
                "en": " ".join(ln["en"] for ln in lines) if lines else e["en"],
                "lines": lines,
                "glossary": [
                    {
                        "form": g["form"],
                        "lemma": g.get("lemma") or g["form"],
                        "en": g["en"],
                    }
                    for g in e.get("glossary") or ()
                ],
            }
        )
    return entries


def passages_hash(entries: list[PassageEntry]) -> str:
    """Stable fingerprint, used to skip no-op DB syncs."""
    payload = json.dumps(entries, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


GameEntry = dict[str, Any]
# keys: slug, game, title, description, pairs[{en,no,note?}]


def load_games(language: str = "nb") -> list[GameEntry]:
    """Game sets from the local JSONL, in authored order.

    Local-only like the passages: a handful of hand-written sets is not worth a
    Parquet snapshot, and the container image carries `data/`.
    """
    path = DATA_DIR / f"games_{language}.jsonl"
    if not path.exists():
        return []
    entries: list[GameEntry] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        entries.append(
            {
                "slug": e["slug"],
                "game": e.get("game", "matching"),
                "title": e["title"],
                "description": e.get("description", ""),
                "pairs": [
                    {"en": p["en"], "no": p["no"], "note": p.get("note")}
                    for p in e["pairs"]
                ],
            }
        )
    return entries


def games_hash(entries: list[GameEntry]) -> str:
    """Stable fingerprint, used to skip no-op DB syncs."""
    payload = json.dumps(entries, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _s3_client(*, fail_fast: bool = False):
    """Shared S3/R2 client.

    `fail_fast` is for callers that have a fallback and would rather give up
    than wait: botocore's defaults spend ~8s retrying an unreachable endpoint,
    which is pure cost when the local deck files are right there. Callers
    without a fallback (audio streaming) keep the default retry behaviour.
    """
    import boto3
    from botocore.config import Config

    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        # Pin the region: R2 requires "auto", MinIO ignores it, and without
        # this an ambient ~/.aws/config region leaks in and breaks R2.
        region_name=settings.s3_region,
        config=Config(connect_timeout=1, read_timeout=2, retries={"max_attempts": 1})
        if fail_fast
        else None,
    )


def deck_object_key(language: str) -> str:
    return f"decks/{language}.parquet"


def load_from_minio(language: str = "nb") -> list[DeckEntry]:
    """Fetch the published Parquet deck and decode it with DuckDB."""
    import duckdb

    settings = get_settings()
    with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
        _s3_client(fail_fast=True).download_fileobj(
            settings.s3_bucket, deck_object_key(language), tmp
        )
        tmp.flush()
        rows = duckdb.sql(
            "SELECT no, en, pos, cefr, type, categories, examples "
            f"FROM read_parquet('{tmp.name}') ORDER BY rank"
        ).fetchall()
    return [
        {
            "no": no,
            "en": en,
            "pos": pos,
            "cefr": cefr,
            "type": typ,
            "categories": list(categories or ()),
            "examples": examples,
        }
        for no, en, pos, cefr, typ, categories, examples in rows
    ]


@lru_cache(maxsize=None)
def load_deck(language: str = "nb") -> tuple[list[DeckEntry], str]:
    """Return (entries, source) preferring the MinIO snapshot over local files.

    Cached for the process lifetime: deck content is immutable while the app
    runs (the sync happens once at startup), and the test suite otherwise
    reloads it once per test. Callers treat the result as read-only.
    """
    try:
        entries = load_from_minio(language)
        return entries, "minio"
    except Exception as exc:  # bucket/object missing, MinIO down, tests, ...
        logger.info("Deck %s not loaded from MinIO (%s); using local files", language, exc)
        return merge_local_deck(language), "local"


def deck_hash(entries: list[DeckEntry]) -> str:
    """Stable fingerprint of deck content, used to skip no-op DB syncs."""
    payload = json.dumps(entries, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()
