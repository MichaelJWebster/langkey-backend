"""Can context rescue recognition? Candidate answers judged by the CTC aligner
(ROADMAP M3 step 5).

A conversation turn knows roughly what the learner will say. This measures
what that is worth before any of it is built, on the takes scripts/bench_stt.py
exported. Every candidate text is scored by how well the audio supports it --
log P(text | audio) under the aligner's wav2vec2 CTC model, divided by the
candidate's letter count, since the raw sum favours shorter texts -- and the
scores are stored, so decision rules can be tried in `score` without
re-running the models. The decision rule: take the best candidate unless
Whisper's own transcript beats it by more than a margin. "Off-script" removes
the target from the set and asks how often a wrong candidate is accepted
anyway -- the failure that matters in a conversation.

Candidate sets, from easiest to hardest:
  * the target + the deck sentences spelt most like it (`run`);
  * the target + one-word near-spellings, Skolen begynner klokka ni / klokke ni
    (`ctc`) -- the worst case for an authored set;
  * Haiku-written alternatives given the question and the target (`llm`);
  * Haiku's guesses given only the question, to see whether the real answer
    can be anticipated before the learner speaks (`llm`);
  * Haiku's proposals given the question AND Whisper's misheard transcript,
    the propose-after-hearing design (`llm`).
Whisper's n-best is also stored (`run`); it adds nothing over greedy, and the
GOP-style threshold against the unconstrained CTC path did worse than
comparing against the transcript, so neither is in the report.

What it cannot measure: a real dialogue. These takes were said against a
sentence on a card.

    uv run python -m scripts.bench_stt export          # if not done already
    uv run python -m scripts.bench_context pool
    HF_HOME=/Volumes/nvm_2tb/home/michaelw/work/huggingface HF_HUB_OFFLINE=1 \\
      /Volumes/nvm_2tb/home/michaelw/work/langkey-speech-venv/bin/python \\
      -m scripts.bench_context run            # Whisper n-best; Metal, ~15 min
    uv run --with anthropic python -m scripts.bench_context llm   # ~750 Haiku calls
    HF_HOME=... HF_HUB_OFFLINE=1 .../langkey-speech-venv/bin/python \\
      -m scripts.bench_context ctc            # scores everything else; CPU, ~3 min
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
LLM_MODEL = "claude-haiku-4-5"  # what a turn would use, for latency


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
            cats = (await conn.execute(text(
                "select t.txt, string_agg(distinct ic.category, ', ') from"
                " (select id item_id, source_text txt from items"
                "  union all select item_id, sentence from item_examples) t"
                " join item_categories ic on ic.item_id = t.item_id group by t.txt"
            ))).all()
        await engine.dispose()
        return sentences, headwords, dict(cats)

    sentences, headwords, cats = asyncio.run(fetch())
    (out / "pool.json").write_text(
        json.dumps({"sentences": sentences, "headwords": headwords, "categories": cats}, ensure_ascii=False)
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


def ctc_scorer(settings):
    """The production scorer (speech_service/scorer.py), so this benchmark
    measures exactly what the conversation turn runs."""
    from speech_service.scorer import make_scorer

    return make_scorer(settings)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as f:
        return [json.loads(line) for line in f]


def load_context(out: Path) -> dict[str, dict]:
    """context.jsonl by take id. Rows written before the ctc step existed
    carry {text, ctc} dicts where plain strings are now stored."""
    def text(x):
        return x["text"] if isinstance(x, dict) else x

    rows = {}
    for r in read_jsonl(out / "context.jsonl"):
        rows[r["id"]] = {**r, "target": text(r["target"]), "distractors": [text(d) for d in r["distractors"]]}
    return rows


def run(out: Path) -> None:
    """Whisper's n-best for every take, with the nearest-deck distractors."""
    import torch

    from app.pronunciation import normalise
    from speech_service import transcriber
    from speech_service.config import SpeechSettings

    settings = SpeechSettings(model="NbAiLab/nb-whisper-medium")
    asr = transcriber.load(settings)

    decks = json.loads((out / "pool.json").read_text())
    path = out / "context.jsonl"
    done = set(load_context(out))
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
        found: list[tuple[str, float]] = []
        seen: set[str] = set()
        for t, s in zip(tok.batch_decode(result.sequences, skip_special_tokens=True), result.sequences_scores):
            key = normalise(t)
            if key and key not in seen:
                seen.add(key)
                found.append((t.strip(), float(s)))
        return found[:NBEST]

    with open(path, "a") as f:
        for n, take in enumerate(takes, 1):
            samples = transcriber.decode((out / "takes" / f"{take['id']}.audio").read_bytes())
            started = time.monotonic()
            guesses = nbest(samples)
            beam_ms = int((time.monotonic() - started) * 1000)
            distractors = nearest(take["target"], decks["sentences" if take["sentence"] else "headwords"], DISTRACTORS)
            f.write(json.dumps({
                "id": take["id"], "target": take["target"], "distractors": distractors,
                "nbest": [{"text": g, "whisper": s} for g, s in guesses], "beam_ms": beam_ms,
            }, ensure_ascii=False) + "\n")
            f.flush()
            if n % 50 == 0:
                print(f"  {n}/{len(takes)}", flush=True)
    print("done")


# --- the LLM half ----------------------------------------------------------------

LLM_SYSTEM = (
    "You design turns for a spoken Norwegian (Bokmål) practice app. The learner is at "
    "CEFR A1 with a vocabulary of about 500 words: greetings, family, home, food, time, "
    "numbers, work, travel, weather. Answer only with JSON, no prose."
)
POSTHOC_SYSTEM = (
    "You interpret speech-recognition output in a spoken Norwegian (Bokmål) practice app. "
    "The learner is at CEFR A1 with about 500 words. The recogniser was trained on native "
    "speech and often mishears a learner: it substitutes similar-sounding fluent Norwegian "
    "words, drops endings, and confuses kj/skj, vowels, and short function words. Answer only "
    "with JSON, no prose."
)


def first_json(text: str):
    """The first JSON object in a reply, whatever surrounds it."""
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[start:])[0]
    except json.JSONDecodeError:
        return None


def llm(out: Path) -> None:
    """Three prompts to Haiku. Per distinct target: a question it answers and
    seven distinct alternatives (sets), and the eight likeliest answers to that
    question alone (anticipation). Per sentence take: what the learner probably
    said, given the question and Whisper's transcript (post hoc). Resumable;
    needs the anthropic package, hence `uv run --with anthropic`."""
    import asyncio

    import anthropic

    pool_ = json.loads((out / "pool.json").read_text())
    cats = pool_.get("categories", {})
    greedy = {r["id"]: r["heard"] for r in read_jsonl(out / "nb-whisper-medium.jsonl")}
    if not greedy:
        sys.exit("needs nb-whisper-medium.jsonl from scripts.bench_stt run")
    client = anthropic.AsyncAnthropic(max_retries=5)
    sem = asyncio.Semaphore(8)

    async def ask(system: str, prompt: str):
        async with sem:
            r = await client.messages.create(model=LLM_MODEL, max_tokens=1024, system=system,
                                             messages=[{"role": "user", "content": prompt}])
        return first_json("".join(b.text for b in r.content if b.type == "text")) or {}

    async def one_set(target: str, is_sentence: bool):
        topic = cats.get(target, "everyday life")
        kind = "sentence" if is_sentence else "word or short phrase"
        a = await ask(LLM_SYSTEM, (
            f"A character in the app asks the learner a question. The learner's answer is this {kind}: "
            f'"{target}" (topic: {topic}).\n'
            "1. Write the character's question in Norwegian, natural and short.\n"
            "2. Write 7 OTHER answers the learner might plausibly give to that question. Each must be a "
            "different meaning, not a rewording or a one-word swap that sounds similar. Include 2 short "
            "fragments (one to three words) and one that says the learner did not understand. Use only "
            "A1 vocabulary.\n"
            'Reply as {"question": "...", "alternatives": ["...", ...]}'
        ))
        if "question" not in a:
            return None
        b = await ask(LLM_SYSTEM, (
            f'A character in the app asks the learner: "{a["question"]}" (topic: {topic}).\n'
            "Write the 8 most likely answers an A1 learner would give, as they would say them: a mix of "
            "full sentences and short fragments, each a different meaning. Use only A1 vocabulary.\n"
            'Reply as {"answers": ["...", ...]}'
        ))
        return {"target": target, "sentence": is_sentence, "question": a["question"],
                "alternatives": [str(x) for x in a.get("alternatives", [])][:7],
                "anticipated": [str(x) for x in b.get("answers", [])][:8]}

    async def one_posthoc(take: dict, question: str):
        obj = await ask(POSTHOC_SYSTEM, (
            f'The character asked: "{question}"\n'
            f'The recogniser heard the learner say: "{greedy[take["id"]]}"\n'
            "What did the learner most likely actually say? Give your single best guess, then 8 "
            "candidates in order of likelihood (the best guess first), each a different meaning or "
            "wording the learner could plausibly have produced, including the transcript taken at "
            "face value if it is plausible, and short fragments where natural. Use A1 vocabulary.\n"
            'Reply as {"best": "...", "candidates": ["...", ...]}'
        ))
        if "best" not in obj:
            return None
        return {"id": take["id"], "best": str(obj["best"]),
                "candidates": [str(c) for c in obj.get("candidates", [])][:8]}

    async def drain(path: Path, coros):
        for coro in asyncio.as_completed(coros):
            try:
                r = await coro
            except Exception as e:  # noqa: BLE001 -- one bad reply must not end the run
                print("skipped:", e, file=sys.stderr)
                continue
            if r:
                # Append-and-close per result (a few hundred tiny writes), so
                # an interrupted run keeps everything it finished.
                with open(path, "a") as f:  # noqa: ASYNC230
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")

    async def main():
        takes = corpus(out)
        targets: dict[str, bool] = {}
        for t in takes:
            targets.setdefault(t["target"], t["sentence"])
        sets_path = out / "llm_sets.jsonl"
        done = {r["target"] for r in read_jsonl(sets_path)}
        todo = [(t, s) for t, s in targets.items() if t not in done]
        print(f"sets: {len(done)} done, {len(todo)} to go", flush=True)
        await drain(sets_path, [one_set(t, s) for t, s in todo])

        question = {r["target"]: r["question"] for r in read_jsonl(sets_path)}
        ph_path = out / "posthoc.jsonl"
        done = {r["id"] for r in read_jsonl(ph_path)}
        todo = [t for t in takes if t["sentence"] and t["target"] in question and t["id"] not in done]
        print(f"post hoc: {len(done)} done, {len(todo)} to go", flush=True)
        await drain(ph_path, [one_posthoc(t, question[t["target"]]) for t in todo])
        print("done")

    asyncio.run(main())


def ctc(out: Path) -> None:
    """CTC-score every candidate text for every take: target, nearest-deck
    distractors, n-best, one-word variants, Whisper's greedy transcript, and
    whatever `llm` produced. Aligner only, on the CPU."""
    from app.pronunciation import normalise
    from speech_service import transcriber
    from speech_service.config import SpeechSettings

    scores = ctc_scorer(SpeechSettings())
    # pool.json is absent for an exported conversation corpus, which brings
    # its own candidate sets (turn_answers.json) instead.
    headwords = json.loads((out / "pool.json").read_text())["headwords"] if (out / "pool.json").exists() else []
    turn_answers = json.loads((out / "turn_answers.json").read_text()) if (out / "turn_answers.json").exists() else {}
    context = load_context(out)
    greedy = {r["id"]: r["heard"] for r in read_jsonl(out / "nb-whisper-medium.jsonl")}
    sets = {r["target"]: r for r in read_jsonl(out / "llm_sets.jsonl")}
    posthoc = {r["id"]: r for r in read_jsonl(out / "posthoc.jsonl")}

    with open(out / "ctc.jsonl", "w") as f:
        for n, take in enumerate(corpus(out), 1):
            i, target = take["id"], take["target"]
            row = context.get(i, {})
            texts = [target, *row.get("distractors", []), *(c["text"] for c in row.get("nbest", []))]
            texts += slot_variants(target, headwords, DISTRACTORS)
            if i in greedy:
                texts.append(greedy[i])
            if target in sets:
                texts += sets[target]["alternatives"] + sets[target]["anticipated"]
            if i in posthoc:
                texts += [posthoc[i]["best"], *posthoc[i]["candidates"]]
            texts += turn_answers.get(i, [])
            distinct = list({normalise(t): t for t in texts if normalise(t)}.values())
            samples = transcriber.decode((out / "takes" / f"{i}.audio").read_bytes())
            started = time.monotonic()
            scored = scores(samples, distinct)
            f.write(json.dumps({
                "id": i,
                "scores": {normalise(t): {"text": t, "ctc": s, "letters": k} for t, (s, k) in zip(distinct, scored)},
                "ms": int((time.monotonic() - started) * 1000),
            }, ensure_ascii=False) + "\n")
            if n % 100 == 0:
                print(f"  {n}", flush=True)
    print("done")


# --- the report -------------------------------------------------------------------


def score(out: Path, raw: bool) -> None:
    from app.pronunciation import SAID, SLIPPED, compare, normalise

    takes = {t["id"]: t for t in corpus(out)}
    context = load_context(out)
    ctc_rows = {r["id"]: r["scores"] for r in read_jsonl(out / "ctc.jsonl")}
    greedy = {r["id"]: r["heard"] for r in read_jsonl(out / "nb-whisper-medium.jsonl")}
    sets = {r["target"]: r for r in read_jsonl(out / "llm_sets.jsonl")}
    posthoc = {r["id"]: r for r in read_jsonl(out / "posthoc.jsonl")}
    # pool.json is absent for an exported conversation corpus, which brings
    # its own candidate sets (turn_answers.json) instead.
    headwords = json.loads((out / "pool.json").read_text())["headwords"] if (out / "pool.json").exists() else []
    turn_answers = json.loads((out / "turn_answers.json").read_text()) if (out / "turn_answers.json").exists() else {}
    if not ctc_rows:
        sys.exit("run the ctc step first")

    def cand(i, text):
        """A scored candidate, or None when the text could not be scored."""
        c = ctc_rows[i].get(normalise(text))
        return c if c and c["ctc"] is not None and c["letters"] else None

    def value(c):
        return c["ctc"] if raw else c["ctc"] / c["letters"]

    def best(i, texts):
        cs = [c for c in (cand(i, t) for t in texts) if c]
        return max(cs, key=value) if cs else None

    def pick(i, expected, margin):
        """The best expected answer unless Whisper's transcript beats it by
        more than `margin` -- the knob between trusting the turn and trusting
        the ear."""
        e, o = best(i, expected), cand(i, greedy[i])
        if e is None:
            return o
        if o is None or value(e) >= value(o) - margin:
            return e
        return o

    def right(i, c):
        return c is not None and normalise(c["text"]) == normalise(takes[i]["target"])

    def gist(i, text):
        ms = compare(takes[i]["target"], text or "")
        return sum(m.verdict in (SAID, SLIPPED) for m in ms) >= GIST_SHARE * len(ms)

    margins = (0, 5, 10, 20) if raw else (0, 0.1, 0.2, 0.3, 0.5)
    unit = "nats" if raw else "nats/letter"
    print(f"CTC scores {'raw' if raw else 'per letter'}; open guess = Whisper medium, greedy\n")

    for label, keep in (("sentences", True), ("headwords", False)):
        ids = [i for i in ctc_rows if takes[i]["sentence"] is keep and i in greedy]
        if not ids:
            continue
        n = len(ids)

        def rate(fn, ids=ids):
            return sum(bool(fn(i)) for i in ids) / len(ids)

        print(f"== {label} ({n})")
        print(f"  free decode, greedy (the app today): exact {rate(lambda i: normalise(greedy[i]) == normalise(takes[i]['target'])):.0%}"
              f", gist {rate(lambda i: gist(i, greedy[i])):.0%}")
        kinds = {
            f"target + {DISTRACTORS} nearest deck sentences": {i: context[i]["distractors"] for i in ids if i in context},
            f"target + {DISTRACTORS} one-word near-spellings": {i: slot_variants(takes[i]["target"], headwords, DISTRACTORS) for i in ids},
        }
        if turn_answers:
            # Real conversation turns: the line's own answers, as the learner met them.
            kinds = {"the line's own answers (conversation turns)": {i: turn_answers[i] for i in ids if i in turn_answers}}
        if sets:
            kinds["target + 7 Haiku alternatives (question + target shown)"] = {
                i: sets[takes[i]["target"]]["alternatives"] for i in ids if takes[i]["target"] in sets}
        for kind, expected in kinds.items():
            sub = [i for i in ids if i in expected]
            if not sub:
                continue
            print(f"  -- {kind} ({len(sub)})")
            print(f"     set alone, CTC picks the target: {rate(lambda i: right(i, best(i, [takes[i]['target'], *expected[i]])), sub):.0%}")
            print(f"     {'margin (' + unit + ')':>22}   exact   gist   off-script wrong taken")
            for m in margins:
                ok = rate(lambda i: right(i, pick(i, [takes[i]["target"], *expected[i]], m)), sub)
                g = rate(lambda i: gist(i, (pick(i, [takes[i]["target"], *expected[i]], m) or {}).get("text")), sub)
                wrong = {i: {normalise(d) for d in expected[i]} for i in sub}

                def took_wrong(i, m=m):
                    c = pick(i, expected[i], m)
                    return c is not None and normalise(c["text"]) in wrong[i]

                fa = rate(took_wrong, sub)
                print(f"     {m:>22}   {ok:>5.0%}  {g:>5.0%}   {fa:>5.0%}")
        print()

    ids = [i for i in ctc_rows if takes[i]["sentence"] and i in greedy]
    if sets:
        sub = [i for i in ids if takes[i]["target"] in sets]
        ant = {i: sets[takes[i]["target"]]["anticipated"] for i in sub}
        hit = sum(any(normalise(a) == normalise(takes[i]["target"]) for a in ant[i]) for i in sub) / len(sub)
        ghit = sum(any(gist(i, a) for a in ant[i]) for i in sub) / len(sub)
        print(f"== anticipation, sentences ({len(sub)}): Haiku's 8 likeliest answers to the question alone")
        print(f"  contain the learner's actual sentence: exactly {hit:.0%}, by gist {ghit:.0%}\n")

    if posthoc:
        sub = [i for i in ids if i in posthoc]
        n = len(sub)
        print(f"== propose after hearing, sentences ({n}): Haiku given the question and Whisper's transcript")
        hb = sum(normalise(posthoc[i]["best"]) == normalise(takes[i]["target"]) for i in sub) / n
        gb = sum(gist(i, posthoc[i]["best"]) for i in sub) / n
        print(f"  Haiku's single best guess, no audio check:      exact {hb:.0%}, gist {gb:.0%}")
        present = sum(any(normalise(c) == normalise(takes[i]["target"]) for c in posthoc[i]["candidates"]) for i in sub) / n
        print(f"  target among its 8 candidates (ceiling):        {present:.0%}")
        for m in margins:
            picks = {i: pick(i, posthoc[i]["candidates"], m) for i in sub}
            ok = sum(right(i, picks[i]) for i in sub) / n
            g = sum(gist(i, (picks[i] or {}).get("text")) for i in sub) / n
            # Confidently wrong: asserted a repaired sentence that is neither
            # the target nor what Whisper heard.
            cw = sum(1 for i in sub if picks[i] and not right(i, picks[i])
                     and normalise(picks[i]["text"]) != normalise(greedy[i])) / n
            print(f"  candidates + transcript, CTC picks, margin {m:>4}: exact {ok:.0%}, gist {g:.0%}, confidently wrong {cw:.0%}")
        print()

    ms = sorted(r["ms"] for r in read_jsonl(out / "ctc.jsonl"))
    beam = sorted(r["beam_ms"] for r in context.values())
    print(f"latency per take, median: CTC scoring {ms[len(ms) // 2]} ms (all candidates)"
          + (f", n-best {beam[len(beam) // 2]} ms" if beam else ""))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    sub = parser.add_subparsers(dest="step", required=True)
    sub.add_parser("pool", help="deck sentences, headwords and categories")
    sub.add_parser("run", help="Whisper n-best and nearest-deck distractors for every take")
    sub.add_parser("llm", help="Haiku answer sets, anticipation and post-hoc proposals")
    sub.add_parser("ctc", help="CTC scores for every candidate text")
    p_score = sub.add_parser("score", help="decision rules over the stored scores")
    p_score.add_argument("--raw", action="store_true", help="raw CTC sums instead of per-letter")
    args = parser.parse_args()
    if args.step == "score":
        score(args.dir, args.raw)
    else:
        {"pool": pool, "run": run, "llm": llm, "ctc": ctc}[args.step](args.dir)


if __name__ == "__main__":
    sys.exit(main())
