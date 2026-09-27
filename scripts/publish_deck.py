"""Publish the deck to MinIO/S3 as Parquet.

Merges the local source files (seed deck + extra examples), converts to
Parquet with DuckDB, and uploads to the content bucket. The API prefers this
snapshot over the bundled files at startup.

Usage: uv run python -m scripts.publish_deck [language]
"""

import json
import sys
import tempfile
from pathlib import Path

import duckdb

from app.config import get_settings
from app.content import _s3_client, deck_object_key, merge_local_deck


def publish(language: str = "nb") -> None:
    entries = merge_local_deck(language)
    for rank, e in enumerate(entries, start=1):
        e["rank"] = rank

    settings = get_settings()
    client = _s3_client()
    existing = {b["Name"] for b in client.list_buckets()["Buckets"]}
    if settings.s3_bucket not in existing:
        client.create_bucket(Bucket=settings.s3_bucket)

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "deck.json"
        parquet = Path(tmp) / "deck.parquet"
        src.write_text(json.dumps(entries, ensure_ascii=False))
        duckdb.sql(
            f"COPY (SELECT * FROM read_json('{src}') ORDER BY rank) TO '{parquet}' (FORMAT parquet)"
        )
        client.upload_file(str(parquet), settings.s3_bucket, deck_object_key(language))

    total_examples = sum(len(e["examples"]) for e in entries)
    print(
        f"Published {len(entries)} items / {total_examples} examples to "
        f"s3://{settings.s3_bucket}/{deck_object_key(language)}"
    )


if __name__ == "__main__":
    publish(sys.argv[1] if len(sys.argv) > 1 else "nb")
