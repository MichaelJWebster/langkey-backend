"""Turn the NoW word list into deck entries, with a gate on every field.

Content pipeline, never the request path (ADR-026). The NTNU "Norwegian on
the Web" A1 list gives a headword and an English gloss; a deck entry also
needs a part of speech, the categories it belongs to, and an example sentence
in both languages. Those come from the local model, and nothing it returns is
trusted: every entry is checked, and the ones that fail are written out
separately rather than quietly imported.

    uv run python -m scripts.build_vocab words.json --out new_entries.json
    uv run python -m scripts.build_vocab words.json --out x.json --limit 10
"""

import argparse
import json
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

from app.categories import CATEGORIES as APP_CATEGORIES

OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "gemma3:12b"

# Ten categories, down from nineteen. A word may belong to several -- "å
# betale" is money and shopping -- and the learner's progress counts the item
# once wherever it appears, because progress lives on the item (ADR-038).
CATEGORIES = {
    "essentials": "pronouns, question words, greetings, politeness, the commonest verbs",
    "people": "family, relationships, describing people, nationalities",
    "home": "the home, furniture, clothes, chores, daily routine",
    "food": "food, drink, cooking, eating out, shopping for food",
    "travel": "places, transport, directions, holidays, countries",
    "work-school": "work, jobs, study, language learning, money, shopping",
    "health": "the body, illness, feelings, sport and exercise",
    "time-numbers": "numbers, days, months, clock time, frequency",
    "nature": "weather, seasons, landscape, animals, outdoors",
    "leisure": "hobbies, sport, music, holidays, celebrations, Norwegian culture",
}

# These are prompt hints, not labels -- the model needs the longer description
# to sort a word. The slugs must still be the application's, or the entries
# this writes would not map onto anything.
assert set(CATEGORIES) == set(APP_CATEGORIES), set(CATEGORIES) ^ set(APP_CATEGORIES)

PROMPT = """You are building an A1 Norwegian (bokmål) flashcard.

Norwegian headword: {no}
English meaning: {en}

Reply with JSON only, with exactly these keys:
  "pos": one of noun, verb, adjective, adverb, pronoun, preposition,
         conjunction, numeral, determiner, interjection, phrase
  "categories": 1 or 2 of {cats} — the ones this word really belongs to
  "ex_no": one short, natural A1 Norwegian sentence using the headword
  "ex_en": a plain English translation of that sentence

The sentence must be something a beginner would actually say, at most 8 words,
and must contain the headword itself.
"""


def ollama_json(model: str, prompt: str) -> dict:
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=json.dumps(
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.2, "num_ctx": 4096},
            }
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(json.loads(r.read())["response"])


def bare(word: str) -> str:
    """The headword without its article or infinitive marker, lower-cased."""
    s = unicodedata.normalize("NFC", word.lower()).strip()
    return re.sub(r"^(en|ei|et|å)\s+", "", s).strip()


# Structural words: their category is their job in the sentence, not the
# subject they happen to be used about.
FUNCTION_WORDS = {
    "pronoun", "preposition", "conjunction", "determiner", "interjection",
}

POS_VALUES = {
    "noun", "verb", "adjective", "adverb", "pronoun", "preposition",
    "conjunction", "numeral", "determiner", "interjection", "phrase",
}


def check(entry: dict, reply: dict) -> list[str]:
    """Everything wrong with one generated entry, as plain sentences."""
    faults = []
    pos = str(reply.get("pos", "")).lower().strip()
    if pos not in POS_VALUES:
        faults.append(f"part of speech {pos!r} is not one of the allowed values")

    cats = reply.get("categories") or []
    if isinstance(cats, str):
        cats = [cats]
    cats = [c for c in cats if c in CATEGORIES]
    if not cats:
        faults.append("no recognised category")

    ex_no = str(reply.get("ex_no", "")).strip()
    ex_en = str(reply.get("ex_en", "")).strip()
    if not ex_no or not ex_en:
        faults.append("missing example sentence")
    else:
        if len(ex_no.split()) > 12:
            faults.append("example is too long for A1")
        # The point of the example is to show the word in use.
        stem = bare(entry["no"])
        if len(stem) > 3 and stem[:-1] not in unicodedata.normalize("NFC", ex_no.lower()):
            faults.append(f"example does not contain {stem!r}")
        if re.search(r"[a-zA-ZæøåÆØÅ]", ex_en) is None:
            faults.append("English example is not English")
    return faults


def generate(entry: dict, model: str, attempts: int = 2) -> tuple[dict | None, list[str]]:
    prompt = PROMPT.format(no=entry["no"], en=entry["en"], cats=", ".join(CATEGORIES))
    faults: list[str] = []
    for _ in range(attempts):
        try:
            reply = ollama_json(model, prompt)
        except Exception as exc:  # noqa: BLE001 - any failure is just a retry
            faults = [f"model call failed: {type(exc).__name__}"]
            continue
        faults = check(entry, reply)
        if faults:
            continue
        pos = str(reply["pos"]).lower().strip()
        cats = reply["categories"]
        if isinstance(cats, str):
            cats = [cats]
        cats = [c for c in cats if c in CATEGORIES]
        # The model sorts content words well and function words badly -- it put
        # "på" in home and time-numbers. Grammar is not a topic: anything whose
        # part of speech makes it structural belongs in essentials, whatever it
        # happens to appear alongside.
        if pos in FUNCTION_WORDS:
            cats = ["essentials"] + [c for c in cats if c != "essentials"]
        if pos == "numeral":
            cats = ["time-numbers"] + [c for c in cats if c != "time-numbers"]
        return (
            {
                "no": entry["no"],
                "en": entry["en"],
                "pos": str(reply["pos"]).lower().strip(),
                "cefr": "A1",
                "categories": [c for c in cats if c in CATEGORIES][:2],
                "ex_no": str(reply["ex_no"]).strip(),
                "ex_en": str(reply["ex_en"]).strip(),
                "source": "ntnu-now",
                "chapter": entry.get("chapter"),
            },
            [],
        )
    return None, faults


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("words", type=Path, help="JSON list of {no, en, chapter}")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    words = json.loads(args.words.read_text())
    if args.limit:
        words = words[: args.limit]

    done: list[dict] = []
    rejected: list[dict] = []
    # Resume: a run of several hundred words should not start over.
    if args.out.exists():
        done = json.loads(args.out.read_text())
        already = {d["no"] for d in done}
        words = [w for w in words if w["no"] not in already]
        print(f"resuming: {len(done)} already built, {len(words)} to go")

    for i, entry in enumerate(words, 1):
        built, faults = generate(entry, args.model)
        if built:
            done.append(built)
        else:
            rejected.append({**entry, "faults": faults})
        if i % 10 == 0 or i == len(words):
            args.out.write_text(json.dumps(done, ensure_ascii=False, indent=1))
            print(f"  {i}/{len(words)}  built {len(done)}  rejected {len(rejected)}", flush=True)

    args.out.write_text(json.dumps(done, ensure_ascii=False, indent=1))
    if rejected:
        bad = args.out.with_suffix(".rejected.json")
        bad.write_text(json.dumps(rejected, ensure_ascii=False, indent=1))
        print(f"\n{len(rejected)} rejected, written to {bad}")
    print(f"{len(done)} entries written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
