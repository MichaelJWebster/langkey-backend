"""Can context rescue recognition? Expected answers plus Whisper's n-best,
judged by the CTC aligner (ROADMAP M3 step 5).

A conversation turn knows roughly what the learner will say. This measures
what that is worth before any of it is built, on the takes scripts/bench_stt.py
exported. For each take the candidates are

  * Whisper's n-best (beam search, five returned) -- the open half, which
    works when the learner goes off-script;
  * an expected-answer set: the true target plus the deck sentences closest
    to it, standing in for the answers a turn would be authored with.

Every candidate is scored by how well the audio supports it -- the CTC
log-likelihood under the aligner's wav2vec2 model, log P(text | audio) over
all alignments -- and the scores are stored, so decision rules can be tried
in `score` without re-running the models. The off-script case drops the
target from the expected set, which is the failure that matters: a fluent
expected answer accepted when the learner said something else.

What it cannot measure: the chat model choosing between candidates with the
conversation in front of it. These takes were said against a sentence on a
card, not in a dialogue.

    uv run python -m scripts.bench_stt export          # if not done already
    uv run python -m scripts.bench_context pool
    HF_HOME=/Volumes/nvm_2tb/home/michaelw/work/huggingface HF_HUB_OFFLINE=1 \\
      /Volumes/nvm_2tb/home/michaelw/work/langkey-speech-venv/bin/python \\
      -m scripts.bench_context run          # then `variants`, the same way
    uv run python -m scripts.bench_context score
"""

# ruff: noqa: B023 -- the lambdas in score() are called inside the loop that defines them
import argparse
import json
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

from scripts.bench_stt import DEFAULT_DIR, GIST_SHARE, corpus

NBEST = 5
# Distractors per take: an expected-answer set of five, the target included.
DISTRACTORS = 4


def pool(out: Path) -> None:
    """Every Norwegian sentence and headword in the deck, for distractors."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.config import get_settings

    async def fetch():
        engine = create_async_engine(get_settings().database_url)
        async with engine.connect() as conn:
            sentences = (await conn.execute(text("select distinct sentence from item_examples"))).scalars().all()
            headwords = (await conn.execute(text("select distinct source_text from items"))).scalars().all()
        await engine.dispose()
        return sentences, headwords

    sentences, headwords = asyncio.run(fetch())
    (out / "pool.json").write_text(
        json.dumps({"sentences": sentences, "headwords": headwords}, ensure_ascii=False)
    )
    print(f"{len(sentences)} sentences, {len(headwords)} headwords")


def nearest(target: str, candidates: list[str], k: int) -> list[str]:
    """The k candidates closest to target in spelling -- the hardest
    distractors the deck has, which is what an authored answer set with
    slot variants (Jeg bor i Oslo / Bergen) looks like to the scorer."""
    from app.pronunciation import normalise

    want = normalise(target)
    seen = {want}
    scored = []
    for c in candidates:
        n = normalise(c)
        if n in seen or not n:
            continue
        seen.add(n)
        scored.append((SequenceMatcher(None, want, n).ratio(), c))
    scored.sort(reverse=True)
    return [c for _, c in scored[:k]]


def ctc_scorer(settings):
    """log P(text | audio) under the aligner's CTC model, for several texts
    against one take; None where a text has no letters the model knows
    (digits) or more letters than the audio has frames."""
    import torch

    from app.pronunciation import words
    from speech_service import aligner, transcriber

    ctc, processor = aligner.load(settings)
    vocab = processor.tokenizer.get_vocab()
    blank = processor.tokenizer.pad_token_id
    delimiter = vocab.get("|")

    def scores(samples, texts: list[str], free: list | None = None) -> list[float | None]:
        """`free`, if given, receives the unconstrained best path's score --
        what the audio supports with no text imposed, the yardstick a
        GOP-style confidence measures a candidate against."""
        with torch.no_grad():
            inputs = processor(samples, sampling_rate=transcriber.SAMPLE_RATE, return_tensors="pt")
            logp = torch.log_softmax(ctc(inputs.input_values).logits[0].float(), dim=-1)
        frames = logp.shape[0]
        if free is not None:
            free.append((float(logp.max(dim=-1).values.sum()), frames))
        out: list[float | None] = []
        for t in texts:
            ids, _ = aligner._tokens(words(t), vocab, delimiter)
            if not ids or len(ids) > frames:
                out.append(None)
                continue
            loss = torch.nn.functional.ctc_loss(
                logp.unsqueeze(1), torch.tensor([ids]), torch.tensor([frames]),
                torch.tensor([len(ids)]), blank=blank, reduction="sum",
            )
            out.append(-float(loss))
        return out

    return scores


def run(out: Path) -> None:
    import torch

    from app.pronunciation import normalise
    from speech_service import transcriber
    from speech_service.config import SpeechSettings

    settings = SpeechSettings(model="NbAiLab/nb-whisper-medium")
    asr = transcriber.load(settings)
    ctc_scores = ctc_scorer(settings)

    decks = json.loads((out / "pool.json").read_text())
    path = out / "context.jsonl"
    done = set()
    if path.exists():
        with open(path) as f:
            done = {json.loads(line)["id"] for line in f}
    takes = [t for t in corpus(out) if t["id"] not in done]
    print(f"{len(done)} done, {len(takes)} to go", flush=True)

    from transformers.generation.utils import GenerationMixin

    tok = asr.tokenizer
    prompt = torch.tensor(
        [tok.convert_tokens_to_ids(["<|startoftranscript|>", "<|no|>", "<|transcribe|>", "<|notimestamps|>"])],
        device=asr.model.device,
    )

    def nbest(samples) -> list[tuple[str, float]]:
        """Up to NBEST distinct transcripts, best first.

        Through GenerationMixin.generate rather than Whisper's own: in
        transformers 5.18 Whisper's override answers num_return_sequences
        with copies of the top beam, identical scores and all (checked on
        CPU and Metal alike). Many beams decode to the same text, so search
        twice as wide as needed and keep the distinct ones.
        """
        features = asr.feature_extractor(samples, sampling_rate=transcriber.SAMPLE_RATE, return_tensors="pt")
        features = features.input_features.to(asr.model.device, asr.model.dtype)
        with torch.no_grad():
            result = GenerationMixin.generate(
                asr.model, input_features=features, decoder_input_ids=prompt,
                num_beams=2 * NBEST, num_return_sequences=2 * NBEST, max_new_tokens=100,
                output_scores=True, return_dict_in_generate=True,
            )
        out: list[tuple[str, float]] = []
        seen: set[str] = set()
        for t, s in zip(tok.batch_decode(result.sequences, skip_special_tokens=True), result.sequences_scores):
            key = normalise(t)
            if key and key not in seen:
                seen.add(key)
                out.append((t.strip(), float(s)))
        return out[:NBEST]

    with open(path, "a") as f:
        for n, take in enumerate(takes, 1):
            samples = transcriber.decode((out / "takes" / f"{take['id']}.audio").read_bytes())
            started = time.monotonic()
            guesses = nbest(samples)
            beam_ms = int((time.monotonic() - started) * 1000)
            distractors = nearest(take["target"], decks["sentences" if take["sentence"] else "headwords"], DISTRACTORS)

            # One CTC score per distinct normalised text, shared by every
            # source it came from.
            texts: list[str] = []
            index: dict[str, int] = {}
            for t in [take["target"], *distractors, *(g for g, _ in guesses)]:
                key = normalise(t)
                if key not in index:
                    index[key] = len(texts)
                    texts.append(t)
            started = time.monotonic()
            scores = ctc_scores(samples, texts)
            ctc_ms = int((time.monotonic() - started) * 1000)

            def entry(t, **extra):
                return {"text": t, "ctc": scores[index[normalise(t)]], **extra}

            f.write(json.dumps({
                "id": take["id"],
                "target": entry(take["target"]),
                "distractors": [entry(d) for d in distractors],
                "nbest": [entry(g, whisper=s) for g, s in guesses],
                "beam_ms": beam_ms, "ctc_ms": ctc_ms,
            }, ensure_ascii=False) + "\n")
            f.flush()
            if n % 50 == 0:
                print(f"  {n}/{len(takes)}", flush=True)
    print("done")


def slot_variants(target: str, headwords: list[str], k: int) -> list[str]:
    """The target with one word swapped for the deck words spelt most like it
    -- Skolen begynner klokka ni / klokke ni. The worst case for an authored
    answer set: real slot variants (Oslo / Bergen) differ more than this.

    The longest word is the slot, as the content word an answer set varies
    is usually the long one.
    """
    from app.pronunciation import normalise

    parts = target.split()
    bare = [normalise(p) for p in parts]
    slot = max(range(len(parts)), key=lambda i: len(bare[i]))
    word = bare[slot]
    singles = {normalise(h) for h in headwords if len(normalise(h).split()) == 1} - {word, ""}
    nearest_words = sorted(singles, key=lambda w: (-SequenceMatcher(None, word, w).ratio(), w))[:k]
    # Keep the slot's punctuation and capital, so only the word differs.
    tail = parts[slot][len(parts[slot].rstrip(",.!?")):]
    out = []
    for w in nearest_words:
        swapped = list(parts)
        swapped[slot] = (w.capitalize() if parts[slot][:1].isupper() else w) + tail
        out.append(" ".join(swapped))
    return out


def variants(out: Path) -> None:
    """CTC scores for one-word variants of each target. Aligner only, so a
    few minutes on the CPU; independent of `run`."""
    from speech_service import transcriber
    from speech_service.config import SpeechSettings

    ctc_scores = ctc_scorer(SpeechSettings())
    headwords = json.loads((out / "pool.json").read_text())["headwords"]
    with open(out / "variants.jsonl", "w") as f:
        for take in corpus(out):
            samples = transcriber.decode((out / "takes" / f"{take['id']}.audio").read_bytes())
            texts = slot_variants(take["target"], headwords, DISTRACTORS)
            free: list[tuple[float, int]] = []
            scored = [{"text": t, "ctc": c} for t, c in zip(texts, ctc_scores(samples, texts, free))]
            f.write(json.dumps({"id": take["id"], "variants": scored, "free": free[0][0],
                                "frames": free[0][1]}, ensure_ascii=False) + "\n")
    print("done")


def score(out: Path, margin: float) -> None:
    from app.pronunciation import SAID, SLIPPED, compare, normalise

    takes = {t["id"]: t for t in corpus(out)}
    with open(out / "context.jsonl") as f:
        rows = [json.loads(line) for line in f]
    greedy: dict[str, str] = {}
    if (out / "nb-whisper-medium.jsonl").exists():
        with open(out / "nb-whisper-medium.jsonl") as f:
            greedy = {r["id"]: r["heard"] for r in map(json.loads, f)}
    # Two kinds of expected-answer set: the nearest deck sentences, and the
    # target with one word swapped (only if `variants` has been run).
    kinds = {"nearest deck sentences": {r["id"]: r["distractors"] for r in rows}}
    yardstick: dict[str, tuple[float, int]] = {}
    if (out / "variants.jsonl").exists():
        with open(out / "variants.jsonl") as f:
            vrows = [json.loads(line) for line in f]
        kinds["one-word variants"] = {r["id"]: r["variants"] for r in vrows}
        yardstick = {r["id"]: (r["free"], r["frames"]) for r in vrows}

    def best(cands):
        cands = [c for c in cands if c["ctc"] is not None]
        return max(cands, key=lambda c: c["ctc"]) if cands else None

    def pick(expected, nbest, m):
        """The best expected answer unless the best open guess beats it by
        more than m nats -- the knob between trusting the turn and trusting
        the ear."""
        e, o = best(expected), best(nbest)
        if e is None:
            return o
        return e if o is None or e["ctc"] >= o["ctc"] - m else o

    def right(r, c):
        return c is not None and normalise(c["text"]) == normalise(r["target"]["text"])

    def gist(r, text):
        ms = compare(r["target"]["text"], text or "")
        return sum(m.verdict in (SAID, SLIPPED) for m in ms) >= GIST_SHARE * len(ms)

    for label, keep in (("sentences", True), ("headwords", False)):
        rs = [r for r in rows if takes[r["id"]]["sentence"] is keep]
        if not rs:
            continue

        def rate(fn, rs=rs):
            return sum(bool(fn(r)) for r in rs) / len(rs)

        print(f"== {label} ({len(rs)})")
        print(f"  {'':44} {'exact':>6} {'gist':>6}")
        if greedy:
            print(f"  {'free decode, greedy (the app today)':44}"
                  f" {rate(lambda r: normalise(greedy[r['id']]) == normalise(r['target']['text'])):>6.0%}"
                  f" {rate(lambda r: gist(r, greedy[r['id']])):>6.0%}")
        print(f"  {'n-best, CTC picks':44} {rate(lambda r: right(r, best(r['nbest']))):>6.0%}"
              f" {rate(lambda r: best(r['nbest']) and gist(r, best(r['nbest'])['text'])):>6.0%}")
        print(f"  {'  target anywhere in the n-best (ceiling)':44}"
              f" {rate(lambda r: any(right(r, c) for c in r['nbest'])):>6.0%}")
        for kind, sets in kinds.items():
            print(f"  -- expected set: target + {DISTRACTORS} {kind}")
            print(f"  {'  expected set alone, CTC picks':44}"
                  f" {rate(lambda r: right(r, best([r['target'], *sets[r['id']]]))):>6.0%}")
            print(f"  {f'  combined with n-best, margin {margin:g}':44}"
                  f" {rate(lambda r: right(r, pick([r['target'], *sets[r['id']]], r['nbest'], margin))):>6.0%}"
                  f" {rate(lambda r: gist(r, (pick([r['target'], *sets[r['id']]], r['nbest'], margin) or {}).get('text'))):>6.0%}")
            print(f"  {'  off-script: a wrong expected answer taken':44}"
                  f" {rate(lambda r: any(pick(sets[r['id']], r['nbest'], margin) is d for d in sets[r['id']])):>6.0%}"
                  f"   (target removed; lower is better)")
        print()

    rs = [r for r in rows if takes[r["id"]]["sentence"]]
    for kind, sets in kinds.items():
        print(f"margin sweep, sentences, {kind}: combined exact / off-script wrong answer taken")
        for m in (-20, -10, -5, 0, 5, 10, 20, 40):
            ok = sum(right(r, pick([r["target"], *sets[r["id"]]], r["nbest"], m)) for r in rs) / len(rs)
            fa = sum(any(pick(sets[r["id"]], r["nbest"], m) is d for d in sets[r["id"]]) for r in rs) / len(rs)
            print(f"  {m:>4}: {ok:>4.0%} / {fa:>4.0%}")
        print()

    def pick_gop(r, expected, tau):
        """Accept the best expected answer when it costs the audio no more
        than tau nats a frame against the unconstrained path; otherwise fall
        back to the open guess. Judges the answer against the audio alone,
        not against whatever Whisper happened to guess."""
        e = best(expected)
        free, frames = yardstick[r["id"]]
        if e is not None and (e["ctc"] - free) / frames >= tau:
            return e
        return best(r["nbest"])

    if yardstick:
        for kind, sets in kinds.items():
            print(f"GOP threshold sweep, sentences, {kind}: combined exact / off-script wrong answer taken")
            for tau in (-0.2, -0.3, -0.4, -0.5, -0.6, -0.8, -1.0):
                ok = sum(right(r, pick_gop(r, [r["target"], *sets[r["id"]]], tau)) for r in rs) / len(rs)
                fa = sum(any(pick_gop(r, sets[r["id"]], tau) is d for d in sets[r["id"]]) for r in rs) / len(rs)
                print(f"  {tau:>5}: {ok:>4.0%} / {fa:>4.0%}")
            print()

    beam = sorted(r["beam_ms"] for r in rows)
    ctc_t = sorted(r["ctc_ms"] for r in rows)
    print(f"latency per take, median: n-best {beam[len(beam) // 2]} ms, CTC scoring {ctc_t[len(ctc_t) // 2]} ms")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    sub = parser.add_subparsers(dest="step", required=True)
    sub.add_parser("pool", help="deck sentences and headwords, for distractors")
    sub.add_parser("run", help="n-best and CTC scores for every take")
    sub.add_parser("variants", help="CTC scores for one-word variants of every target")
    p_score = sub.add_parser("score", help="decision rules over the stored scores")
    p_score.add_argument("--margin", type=float, default=0.0,
                         help="nats an open guess must win by to override an expected answer")
    args = parser.parse_args()
    steps = {"pool": pool, "run": run, "variants": variants}
    if args.step == "score":
        score(args.dir, args.margin)
    else:
        steps[args.step](args.dir)


if __name__ == "__main__":
    sys.exit(main())
