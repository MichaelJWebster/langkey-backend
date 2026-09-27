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
from pathlib import Path
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"

DeckEntry = dict[str, Any]  # keys: no, en, pos, cefr, type, examples[{no,en}]


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
                "examples": examples,
            }
        )
    return entries


def _s3_client():
    import boto3

    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
    )


def deck_object_key(language: str) -> str:
    return f"decks/{language}.parquet"


def load_from_minio(language: str = "nb") -> list[DeckEntry]:
    """Fetch the published Parquet deck and decode it with DuckDB."""
    import duckdb

    settings = get_settings()
    with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
        _s3_client().download_fileobj(settings.s3_bucket, deck_object_key(language), tmp)
        tmp.flush()
        rows = duckdb.sql(
            f"SELECT no, en, pos, cefr, type, examples FROM read_parquet('{tmp.name}') "
            "ORDER BY rank"
        ).fetchall()
    return [
        {"no": no, "en": en, "pos": pos, "cefr": cefr, "type": typ, "examples": examples}
        for no, en, pos, cefr, typ, examples in rows
    ]


def load_deck(language: str = "nb") -> tuple[list[DeckEntry], str]:
    """Return (entries, source) preferring the MinIO snapshot over local files."""
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
