"""Benchmark a speech model on every saved take (ROADMAP M3 step 2).

Three steps, because they need two different environments. The export reads
Postgres and S3 with the API's .venv; the run needs torch, which only the
speech venv has (ADR-032); the score needs neither.

    uv run python -m scripts.bench_stt export
    HF_HOME=/Volumes/nvm_2tb/home/michaelw/work/huggingface HF_HUB_OFFLINE=1 \\
      /Volumes/nvm_2tb/home/michaelw/work/langkey-speech-venv/bin/python \\
      -m scripts.bench_stt run NbAiLab/nb-whisper-large
    uv run python -m scripts.bench_stt score nb-whisper-medium nb-whisper-large

The run goes through speech_service/transcriber.py, so it measures what the
app would hear and not a hand-rolled pipeline: on 2026-10-24 a fresh medium
run matched all 547 transcripts the app had stored. It appends one line per
take and skips takes already done, so an interrupted run resumes.

Takes are user data, so they go to --dir (outside the repo), never data/.
Stop the speech service first if you care about the timings: two models on
one Metal device slow each other down.
"""

import argparse
import csv
import json
import statistics
import sys
import time
from math import comb
from pathlib import Path

DEFAULT_DIR = Path("/tmp/langkey-stt-bench")

# A sentence keeps its gist when at least this share of its target words came
# through, exactly or nearly. The conversation feature needs the meaning, not
# the spelling, so this is the number that matters for it.
GIST_SHARE = 0.8


def export(out: Path) -> None:
    """Every take with the sentence it was meant to be, plus its audio."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.config import get_settings
    from app.content import _s3_client

    settings = get_settings()

    async def rows():
        engine = create_async_engine(settings.database_url)
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "select r.id, r.object_key, coalesce(e.sentence, i.source_text),"
                    " r.example_id is not null, coalesce(r.transcript, ''),"
                    " r.transcript_model"
                    " from user_recordings r join items i on i.id = r.item_id"
                    " left join item_examples e on e.id = r.example_id"
                    " order by r.created_at"
                )
            )
            found = result.all()
        await engine.dispose()
        return found

    found = asyncio.run(rows())
    (out / "takes").mkdir(parents=True, exist_ok=True)
    with open(out / "corpus.tsv", "w", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        for rid, key, target, is_sentence, stored, stored_model in found:
            writer.writerow([rid, key, target, int(is_sentence), stored, stored_model or ""])

    s3 = _s3_client()
    fetched = 0
    for rid, key, *_ in found:
        path = out / "takes" / f"{rid}.audio"
        if not path.exists():
            s3.download_file(settings.s3_bucket, key, str(path))
            fetched += 1
    print(f"{len(found)} takes in {out}, {fetched} fetched now")


def corpus(out: Path) -> list[dict]:
    with open(out / "corpus.tsv") as f:
        return [
            {"id": r[0], "target": r[2], "sentence": r[3] == "1", "stored": r[4], "stored_model": r[5]}
            for r in csv.reader(f, delimiter="\t")
        ]


def results_path(out: Path, model: str) -> Path:
    return out / f"{model.split('/')[-1]}.jsonl"


def run(out: Path, model: str) -> None:
    from speech_service import transcriber
    from speech_service.config import SpeechSettings

    settings = SpeechSettings(model=model)
    transcriber.load(settings)

    path = results_path(out, model)
    done = set()
    if path.exists():
        with open(path) as f:
            done = {json.loads(line)["id"] for line in f}
    takes = [t for t in corpus(out) if t["id"] not in done]
    print(f"{model}: {len(done)} done, {len(takes)} to go", flush=True)

    with open(path, "a") as f:
        for n, take in enumerate(takes, 1):
            audio = (out / "takes" / f"{take['id']}.audio").read_bytes()
            started = time.monotonic()
            try:
                heard, error = transcriber.transcribe(audio, settings)["text"], None
            except Exception as e:  # noqa: BLE001 -- one bad take must not lose the run
                heard, error = "", str(e)
            ms = int((time.monotonic() - started) * 1000)
            f.write(json.dumps({"id": take["id"], "heard": heard, "ms": ms, "error": error},
                               ensure_ascii=False) + "\n")
            f.flush()
            if n % 100 == 0:
                print(f"  {n}/{len(takes)}", flush=True)
    print(f"done {model}")


def sign_test(a: int, b: int) -> float:
    """Two-sided p for a paired win/loss count, ties dropped."""
    n, k = a + b, min(a, b)
    return min(1.0, 2 * sum(comb(n, j) for j in range(k + 1)) / 2**n) if n else 1.0


def score(out: Path, models: list[str]) -> None:
    from app.pronunciation import SAID, SLIPPED, compare, normalise

    takes = {t["id"]: t for t in corpus(out)}
    runs = {}
    for model in models:
        with open(results_path(out, model)) as f:
            runs[model] = {r["id"]: r for r in map(json.loads, f)}
    common = sorted(set(takes).intersection(*runs.values()))
    print(f"{len(common)} takes in common\n")

    def marks(take, r):
        return compare(take["target"], r["heard"])

    def exact(take, r):
        return normalise(take["target"]) == normalise(r["heard"])

    def gist(take, r):
        ms = marks(take, r)
        return sum(m.verdict in (SAID, SLIPPED) for m in ms) >= GIST_SHARE * len(ms)

    for label, keep in (("sentences", True), ("headwords", False)):
        ids = [i for i in common if takes[i]["sentence"] is keep]
        if not ids:
            continue
        print(f"== {label} ({len(ids)})")
        print(f"{'model':24} {'exact':>6} {'said':>6} {'+slip':>6} {'gist':>6} {'median ms':>10} {'p90 ms':>7}")
        for model, run_ in runs.items():
            said = slipped = total = 0
            for i in ids:
                for m in marks(takes[i], run_[i]):
                    total += 1
                    said += m.verdict == SAID
                    slipped += m.verdict == SLIPPED
            ms = sorted(run_[i]["ms"] for i in ids)
            print(
                f"{model:24} {sum(exact(takes[i], run_[i]) for i in ids) / len(ids):>6.0%}"
                f" {said / total:>6.0%} {(said + slipped) / total:>6.0%}"
                f" {sum(gist(takes[i], run_[i]) for i in ids) / len(ids):>6.0%}"
                f" {statistics.median(ms):>10.0f} {ms[int(len(ms) * 0.9)]:>7}"
            )
        print()

    if len(runs) == 2:
        (ma, a), (mb, b) = runs.items()
        sentences = [i for i in common if takes[i]["sentence"]]
        for label, test in (("exact", exact), ("gist", gist)):
            won = [i for i in sentences if test(takes[i], b[i]) and not test(takes[i], a[i])]
            lost = [i for i in sentences if test(takes[i], a[i]) and not test(takes[i], b[i])]
            print(f"{label}: {mb} better on {len(won)}, {ma} better on {len(lost)},"
                  f" sign test p={sign_test(len(won), len(lost)):.2f}")
        neither = sum(not gist(takes[i], a[i]) and not gist(takes[i], b[i]) for i in sentences)
        print(f"gist lost by both: {neither}/{len(sentences)}\n")

        for label, ids in (
            (f"{mb} right, {ma} not", [i for i in sentences if exact(takes[i], b[i]) and not exact(takes[i], a[i])]),
            (f"{ma} right, {mb} not", [i for i in sentences if exact(takes[i], a[i]) and not exact(takes[i], b[i])]),
        ):
            print(f"-- {label} (first 10 of {len(ids)})")
            for i in ids[:10]:
                print(f"  {takes[i]['target']}\n    {ma}: {a[i]['heard']}\n    {mb}: {b[i]['heard']}")
            print()

    for model, run_ in runs.items():
        stored = [i for i in common if takes[i]["stored_model"].endswith(model)]
        if stored:
            same = sum(run_[i]["heard"].strip() == takes[i]["stored"].strip() for i in stored)
            print(f"control: {model} matches {same}/{len(stored)} transcripts the app stored")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR, help=f"working directory (default {DEFAULT_DIR})")
    sub = parser.add_subparsers(dest="step", required=True)
    sub.add_parser("export", help="takes and their targets, from Postgres and S3")
    p_run = sub.add_parser("run", help="transcribe every take with one model")
    p_run.add_argument("model")
    p_score = sub.add_parser("score", help="compare one or more runs")
    p_score.add_argument("models", nargs="+", help="as given to run, with or without the NbAiLab/ prefix")
    args = parser.parse_args()

    if args.step == "export":
        export(args.dir)
    elif args.step == "run":
        run(args.dir, args.model)
    else:
        score(args.dir, [m.split("/")[-1] for m in args.models])


if __name__ == "__main__":
    sys.exit(main())
