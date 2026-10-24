"""Export confirmed conversation turns as a benchmark corpus.

The conversation feature logs every spoken turn with the answer the learner
confirmed (docs/plans/conversation-corpus.md). This writes those turns in the
layout scripts/bench_stt.py exports -- corpus.tsv plus takes/<id>.audio --
with the confirmed answer as the target, and turn_answers.json holding each
turn's candidate set (the line's own answers plus the universal ones). Then
the benchmark runs unchanged on real replies instead of sentences read after
a reference:

    uv run python -m scripts.export_conversations --dir /tmp/langkey-conv-bench
    HF_HOME=... HF_HUB_OFFLINE=1 .../langkey-speech-venv/bin/python \\
      -m scripts.bench_stt --dir /tmp/langkey-conv-bench run NbAiLab/nb-whisper-medium
    HF_HOME=... .../langkey-speech-venv/bin/python \\
      -m scripts.bench_context --dir /tmp/langkey-conv-bench ctc
    uv run python -m scripts.bench_context --dir /tmp/langkey-conv-bench score

Only turns with a confirmed answer are exported; "none of these" turns (typed)
are counted and reported, since their rate is the real off-script rate the
decision rule was tuned against in simulation. Turns abandoned with "try
again" carry no label and are skipped.

Takes are user data: --dir must be outside the repo.
"""

import argparse
import asyncio
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.content import _s3_client
from app.conversation import UNIVERSAL
from app.db import session_factory
from app.models import Conversation, ConversationTurn, Dialogue

DEFAULT_DIR = Path("/tmp/langkey-conv-bench")


async def rows(since: datetime | None):
    async with session_factory() as session:
        query = (
            select(ConversationTurn, Dialogue)
            .join(Conversation, Conversation.id == ConversationTurn.conversation_id)
            .join(Dialogue, Dialogue.id == Conversation.dialogue_id)
            .order_by(ConversationTurn.created_at)
        )
        if since:
            query = query.where(ConversationTurn.created_at >= since)
        return (await session.execute(query)).all()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--since", type=datetime.fromisoformat, help="only turns from this date (ISO)")
    args = parser.parse_args()
    if Path(__file__).parent.parent in args.dir.resolve().parents:
        raise SystemExit("--dir must be outside the repo: these are the learner's recordings")

    found = asyncio.run(rows(args.since))
    universal = {i: t for i, t, _ in UNIVERSAL}
    args.dir.joinpath("takes").mkdir(parents=True, exist_ok=True)
    s3 = _s3_client()
    bucket = get_settings().s3_bucket

    tally: Counter = Counter()
    turn_answers: dict[str, list[str]] = {}
    with open(args.dir / "corpus.tsv", "w", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        for turn, dialogue in found:
            tally[f"decision {turn.decision}"] += 1
            tally[f"confirmed {turn.confirmed_how or 'never'}"] += 1
            node = dialogue.nodes.get(turn.node_id)
            if node is None or turn.confirmed_id is None:
                continue
            slots = {a["id"] for a in node["answers"] if a.get("match")}
            if turn.confirmed_id in slots:
                # "Jeg heter …": the value is the learner's own, so there is
                # no fixed target to benchmark against.
                tally["slot answers (not exported)"] += 1
                continue
            answers = {a["id"]: a["text"] for a in node["answers"] if not a.get("match")} | universal
            target = answers.get(turn.confirmed_id)
            if target is None:
                continue  # the answer has since been renamed in the content
            tid = str(turn.id)
            # Column 4 is "is a sentence": every answer is treated as one.
            writer.writerow([tid, turn.object_key, target, 1, turn.transcript or "", turn.transcript_model or ""])
            turn_answers[tid] = [t for t in answers.values() if t != target]
            path = args.dir / "takes" / f"{tid}.audio"
            if not path.exists():
                s3.download_file(bucket, turn.object_key, str(path))
            tally["exported"] += 1
            tally["first guess right" if turn.picked_id == turn.confirmed_id else "first guess wrong or none"] += 1

    (args.dir / "turn_answers.json").write_text(json.dumps(turn_answers, ensure_ascii=False))
    print(f"{len(found)} turns in the log; {tally['exported']} exported to {args.dir}")
    for key, count in sorted(tally.items()):
        print(f"  {key:28} {count}")


if __name__ == "__main__":
    main()
