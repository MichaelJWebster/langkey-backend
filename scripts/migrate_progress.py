"""Copy one user's learning progress from a source database into a target one.

Built for moving local study progress onto the deployed Neon database, which
holds the same published deck but its own row ids. Nothing is matched on id:
items map by (language_code, source_text, item_type), examples by their
sentence within an item, and the user by email on each side.

Merge, never replace: a card that exists on both sides keeps whichever row was
reviewed most recently, and review events / attempts / glossary rows are
inserted only when the target has no equivalent row. Re-running is therefore
safe. Prints a plan and writes a JSON backup of every target row it would
touch; pass --apply to actually write.

    uv run python scripts/migrate_progress.py \
        --source postgresql://langkey:langkey@localhost:5433/langkey \
        --target "$LANGKEY_DATABASE_URL" \
        --source-email dev@langkey.dev --target-email me@example.com \
        --backup /tmp/prod-progress-backup.json [--apply]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import datetime
from typing import Any

import asyncpg


def _clean_dsn(url: str) -> str:
    """asyncpg takes a plain postgres:// DSN without SQLAlchemy's driver part."""
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    # asyncpg rejects libpq-only params that Neon's URLs carry.
    for param in ("sslmode", "channel_binding"):
        url = url.replace(f"&{param}=require", "").replace(f"?{param}=require", "?")
    return url.replace("?&", "?").rstrip("?")


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, uuid.UUID)):
        return str(value)
    raise TypeError(f"not JSON serialisable: {type(value)}")


async def _user_id(conn: asyncpg.Connection, email: str) -> uuid.UUID:
    row = await conn.fetchrow("select id from users where email = $1", email)
    if row is None:
        raise SystemExit(f"no user {email!r} in that database")
    return row["id"]


async def _item_maps(conn: asyncpg.Connection) -> tuple[dict, dict]:
    """(natural key -> item id), (item id + sentence -> example id)."""
    items = {
        (r["language_code"], r["source_text"], r["item_type"]): r["id"]
        for r in await conn.fetch("select id, language_code, source_text, item_type from items")
    }
    examples = {
        (r["item_id"], r["sentence"]): r["id"]
        for r in await conn.fetch("select id, item_id, sentence from item_examples")
    }
    return items, examples


async def migrate(args: argparse.Namespace) -> None:
    src = await asyncpg.connect(_clean_dsn(args.source))
    dst = await asyncpg.connect(_clean_dsn(args.target))
    try:
        src_user = await _user_id(src, args.source_email)
        dst_user = await _user_id(dst, args.target_email)

        src_items, src_examples = await _item_maps(src)
        dst_items, dst_examples = await _item_maps(dst)
        src_item_key = {v: k for k, v in src_items.items()}
        src_example_key = {v: k for k, v in src_examples.items()}

        def map_item(item_id: int | None) -> int | None:
            key = src_item_key.get(item_id) if item_id is not None else None
            return dst_items.get(key) if key else None

        def map_example(example_id: int | None) -> int | None:
            if example_id is None:
                return None
            key = src_example_key.get(example_id)
            if key is None:
                return None
            mapped_item = map_item(key[0])
            return dst_examples.get((mapped_item, key[1])) if mapped_item else None

        # --- cards ------------------------------------------------------------
        src_cards = await src.fetch(
            "select * from user_cards where user_id = $1", src_user
        )
        dst_cards = {
            (r["item_id"], r["direction"]): r
            for r in await dst.fetch("select * from user_cards where user_id = $1", dst_user)
        }

        insert_cards: list[asyncpg.Record] = []
        update_cards: list[tuple[asyncpg.Record, asyncpg.Record]] = []
        card_id_map: dict[uuid.UUID, uuid.UUID] = {}
        unmapped_items = 0
        for card in src_cards:
            item_id = map_item(card["item_id"])
            if item_id is None:
                unmapped_items += 1
                continue
            existing = dst_cards.get((item_id, card["direction"]))
            if existing is None:
                new_id = uuid.uuid4()
                card_id_map[card["id"]] = new_id
                insert_cards.append((card, item_id, new_id))
            else:
                card_id_map[card["id"]] = existing["id"]
                # Keep the more recently studied side.
                src_seen, dst_seen = card["last_review"], existing["last_review"]
                if src_seen is not None and (dst_seen is None or src_seen > dst_seen):
                    update_cards.append((card, existing))

        # --- review events ----------------------------------------------------
        src_events = await src.fetch(
            "select * from review_events where user_id = $1 order by reviewed_at", src_user
        )
        existing_events = {
            (r["user_card_id"], r["reviewed_at"])
            for r in await dst.fetch(
                "select user_card_id, reviewed_at from review_events where user_id = $1", dst_user
            )
        }
        new_events = [
            (e, card_id_map[e["user_card_id"]])
            for e in src_events
            if e["user_card_id"] in card_id_map
            and (card_id_map[e["user_card_id"]], e["reviewed_at"]) not in existing_events
        ]

        # --- exercise attempts -------------------------------------------------
        src_attempts = await src.fetch(
            "select * from exercise_attempts where user_id = $1 order by created_at", src_user
        )
        existing_attempts = {
            r["created_at"]
            for r in await dst.fetch(
                "select created_at from exercise_attempts where user_id = $1", dst_user
            )
        }
        new_attempts = [a for a in src_attempts if a["created_at"] not in existing_attempts]

        # --- glossary ----------------------------------------------------------
        src_gloss = await src.fetch("select * from user_glossary where user_id = $1", src_user)
        existing_gloss = {
            r["item_id"]
            for r in await dst.fetch(
                "select item_id from user_glossary where user_id = $1", dst_user
            )
        }
        new_gloss = []
        for g in src_gloss:
            item_id = map_item(g["item_id"])
            if item_id is not None and item_id not in existing_gloss:
                new_gloss.append((g, item_id))

        # --- reading positions --------------------------------------------------
        src_positions = await src.fetch(
            "select p.*, t.slug from user_reading_positions p join texts t on t.id = p.text_id"
            " where p.user_id = $1",
            src_user,
        )
        dst_text_ids = {r["slug"]: r["id"] for r in await dst.fetch("select id, slug from texts")}

        print(
            f"source {args.source_email}: {len(src_cards)} cards, {len(src_events)} reviews, "
            f"{len(src_attempts)} attempts, {len(src_gloss)} glossary, "
            f"{len(src_positions)} reading positions"
        )
        print(
            f"plan for {args.target_email}: insert {len(insert_cards)} cards, "
            f"update {len(update_cards)} cards, insert {len(new_events)} reviews, "
            f"{len(new_attempts)} attempts, {len(new_gloss)} glossary rows, "
            f"{len(src_positions)} reading positions"
        )
        if unmapped_items:
            print(f"  warning: {unmapped_items} cards skipped — item not in the target deck")

        if args.backup:
            payload = {
                "target_email": args.target_email,
                "taken_at": datetime.now().isoformat(),
                "user_cards": [dict(r) for r in dst_cards.values()],
                "review_events": [
                    dict(r)
                    for r in await dst.fetch(
                        "select * from review_events where user_id = $1", dst_user
                    )
                ],
                "exercise_attempts": [
                    dict(r)
                    for r in await dst.fetch(
                        "select * from exercise_attempts where user_id = $1", dst_user
                    )
                ],
                "user_glossary": [
                    dict(r)
                    for r in await dst.fetch(
                        "select * from user_glossary where user_id = $1", dst_user
                    )
                ],
            }
            with open(args.backup, "w") as fh:
                json.dump(payload, fh, default=_json_default, indent=1)
            print(f"  backup of the target's current rows written to {args.backup}")

        if not args.apply:
            print("dry run — pass --apply to write")
            return

        async with dst.transaction():
            for card, item_id, new_id in insert_cards:
                await dst.execute(
                    "insert into user_cards (id, user_id, item_id, direction, fsrs_state,"
                    " fsrs_step, stability, difficulty, due, last_review, reps, lapses, created_at)"
                    " values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)",
                    new_id, dst_user, item_id, card["direction"], card["fsrs_state"],
                    card["fsrs_step"], card["stability"], card["difficulty"], card["due"],
                    card["last_review"], card["reps"], card["lapses"], card["created_at"],
                )
            for card, existing in update_cards:
                await dst.execute(
                    "update user_cards set fsrs_state=$1, fsrs_step=$2, stability=$3,"
                    " difficulty=$4, due=$5, last_review=$6, reps=$7, lapses=$8 where id=$9",
                    card["fsrs_state"], card["fsrs_step"], card["stability"], card["difficulty"],
                    card["due"], card["last_review"], card["reps"], card["lapses"], existing["id"],
                )
            for event, card_id in new_events:
                await dst.execute(
                    "insert into review_events (user_id, user_card_id, rating, reviewed_at,"
                    " was_new, due_after, stability_after, difficulty_after)"
                    " values ($1,$2,$3,$4,$5,$6,$7,$8)",
                    dst_user, card_id, event["rating"], event["reviewed_at"], event["was_new"],
                    event["due_after"], event["stability_after"], event["difficulty_after"],
                )
            for attempt in new_attempts:
                await dst.execute(
                    "insert into exercise_attempts (user_id, exercise_type, item_id, example_id,"
                    " correct, near_miss, hints, score, speed, answer, created_at)"
                    " values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                    dst_user, attempt["exercise_type"], map_item(attempt["item_id"]),
                    map_example(attempt["example_id"]), attempt["correct"], attempt["near_miss"],
                    attempt["hints"], attempt["score"], attempt["speed"], attempt["answer"],
                    attempt["created_at"],
                )
            for gloss, item_id in new_gloss:
                await dst.execute(
                    "insert into user_glossary (user_id, item_id, via, first_seen_at)"
                    " values ($1,$2,$3,$4) on conflict (user_id, item_id) do nothing",
                    dst_user, item_id, gloss["via"], gloss["first_seen_at"],
                )
            for position in src_positions:
                text_id = dst_text_ids.get(position["slug"])
                if text_id is None:
                    continue
                await dst.execute(
                    "insert into user_reading_positions (user_id, text_id, seq, updated_at)"
                    " values ($1,$2,$3,$4) on conflict (user_id, text_id) do update"
                    " set seq = greatest(user_reading_positions.seq, excluded.seq),"
                    " updated_at = excluded.updated_at",
                    dst_user, text_id, position["seq"], position["updated_at"],
                )
        print("applied")
    finally:
        await src.close()
        await dst.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="source DSN")
    parser.add_argument("--target", required=True, help="target DSN")
    parser.add_argument("--source-email", required=True)
    parser.add_argument("--target-email", required=True)
    parser.add_argument("--backup", help="write the target's current rows here first")
    parser.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    asyncio.run(migrate(parser.parse_args()))


if __name__ == "__main__":
    main()
