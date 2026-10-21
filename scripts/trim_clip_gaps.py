"""Move the existing clips to the trimmed format (CLIP_FORMAT, app/audio.py).

Every clip gets a new key, because the format is part of the key. Each one is
copied from its old key with its leading silence capped at LEADING_GAP_MAX,
and its cached word alignment (ADR-043) is copied with it, shifted earlier by
however much was cut. The shift is exact -- trimming only removes samples
before the first word -- so no clip has to go back through the aligner.

Additive by default: old keys and old alignment rows stay, so the deployed
code keeps serving until the new code is live. Afterwards:

    uv run python -m scripts.trim_clip_gaps             # copy + trim (idempotent)
    uv run python -m scripts.trim_clip_gaps --dry-run   # report only
    uv run python -m scripts.trim_clip_gaps --prune     # after deploy: drop the old keys

It also rewrites the keys in data/audio_onset_issues.json, which the onset
gate matches by key.
"""

import argparse
import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import delete, select

from app.audio import (
    LENGTH_SCALE,
    PRONUNCIATION_SCALES,
    audio_object_key,
    trim_leading_silence,
    voice_for_text,
)
from app.config import get_settings
from app.content import _s3_client, load_deck
from app.db import session_factory
from app.models import ClipAlignment
from scripts.check_audio_onsets import ISSUES_PATH


def legacy_key(language: str, text: str, primary: bool, scale: float) -> str:
    """The key before CLIP_FORMAT joined the digest."""
    voice = voice_for_text(text, primary)
    digest = hashlib.sha256(f"{voice}|{scale}|{text.strip()}".encode()).hexdigest()[:20]
    return f"audio/{language}/{digest}.wav"


def variants(language: str) -> set[tuple[str, bool, float]]:
    """Every (text, primary, scale) the generator voices (docker/piper)."""
    entries, _ = load_deck(language)
    out: set[tuple[str, bool, float]] = set()
    for entry in entries:
        out.add((entry["no"], True, LENGTH_SCALE))
        out.update((entry["no"], True, s) for s in PRONUNCIATION_SCALES)
        for ex in entry["examples"]:
            out.add((ex["no"], False, LENGTH_SCALE))
            out.update((ex["no"], True, s) for s in PRONUNCIATION_SCALES)
    return out


def shifted(words: list[dict | None], cut: float) -> list[dict | None]:
    return [
        None
        if w is None
        else {**w, "start": round(max(0.0, w["start"] - cut), 3), "end": round(max(0.0, w["end"] - cut), 3)}
        for w in words
    ]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", default="nb")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--prune", action="store_true", help="delete the old keys and their alignments")
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()

    bucket = get_settings().s3_bucket
    client = _s3_client()
    moves = {
        legacy_key(args.language, t, p, s): audio_object_key(args.language, t, primary=p, scale=s)
        for t, p, s in variants(args.language)
    }
    have: set[str] = set()
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=f"audio/{args.language}/"
    ):
        have.update(o["Key"] for o in page.get("Contents", []))

    if args.prune:
        old = sorted(k for k in moves if k in have and moves[k] in have)
        print(f"pruning {len(old)} old clips (each has its trimmed copy)")
        if not args.dry_run:
            for i in range(0, len(old), 1000):
                client.delete_objects(
                    Bucket=bucket, Delete={"Objects": [{"Key": k} for k in old[i : i + 1000]]}
                )
            async with session_factory() as session:
                await session.execute(delete(ClipAlignment).where(ClipAlignment.object_key.in_(old)))
                await session.commit()
        return

    todo = [(old, new) for old, new in moves.items() if old in have and new not in have]
    print(f"{len(moves)} clips in the deck, {len(todo)} to move")

    def move(pair: tuple[str, str]) -> tuple[str, str, float]:
        old, new = pair
        data = client.get_object(Bucket=bucket, Key=old)["Body"].read()
        clip, cut = trim_leading_silence(data)
        if not args.dry_run:
            client.put_object(Bucket=bucket, Key=new, Body=clip, ContentType="audio/wav")
        return old, new, cut

    with ThreadPoolExecutor(args.workers) as pool:
        done = list(pool.map(move, todo))
    cuts = {old: (new, cut) for old, new, cut in done}
    trimmed = sorted((c for _, c in cuts.values() if c > 0), reverse=True)
    print(f"trimmed {len(trimmed)}" + (f", longest cut {trimmed[0]:.2f}s" if trimmed else ""))

    async with session_factory() as session:
        rows = (
            await session.scalars(select(ClipAlignment).where(ClipAlignment.object_key.in_(list(cuts))))
        ).all()
        taken = set(
            await session.scalars(
                select(ClipAlignment.object_key).where(
                    ClipAlignment.object_key.in_([n for n, _ in cuts.values()])
                )
            )
        )
        added = 0
        for row in rows:
            new, cut = cuts[row.object_key]
            if new in taken:
                continue
            session.add(
                ClipAlignment(object_key=new, text=row.text, words=shifted(row.words, cut), model=row.model)
            )
            added += 1
        print(f"alignments carried over: {added}")
        if not args.dry_run:
            await session.commit()

    issues = json.loads(ISSUES_PATH.read_text())
    for clip in issues["clips"]:
        clip["key"] = audio_object_key(
            args.language, clip["text"], primary=clip["primary"], scale=clip["scale"]
        )
    if not args.dry_run:
        ISSUES_PATH.write_text(json.dumps(issues, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
