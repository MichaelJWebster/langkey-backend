"""Gates for the conversation dialogues (docs/plans/conversation-corpus.md).

Deterministic checks a dialogue must pass before it reaches the learner, in
the spirit of the translation and audio gates (ADR-028, ADR-029). Run on the
shipped file by tests/test_dialogues.py, and by hand on a draft:

    uv run python -m scripts.check_dialogues                 # data/dialogues_nb.json
    uv run python -m scripts.check_dialogues path/to/draft.json

Failures (any one stops the file):

* **structure** -- the start node exists, every `next` names a node or is
  null, every node is reachable, the tree has no cycles, ids are unique.
* **length** -- every path from start to an end has between MIN_TURNS and
  MAX_TURNS learner turns.
* **distinct** -- no two answers to one line, the universal "Jeg forstår
  ikke." and "Kan du gjenta?" included, are the same words, or the same words
  but one where the odd ones out are spelt alike (SequenceMatcher >= 0.6):
  the near-spelling case the 2026-10-24 benchmark showed the scorer cannot
  separate (83% from the set alone, against 95-96% for distinct sets).
* **order** -- levels rise strictly with position, so the list the learner
  sees runs from easiest to hardest.
* **slots** -- a slot answer ("Jeg heter …", for a value only the learner
  knows) has a pattern that compiles and matches its own example.

Reported, not failed: words in a learner answer that the deck does not
contain. A dialogue may teach a word; it should not do so by accident.
"""

import json
import re
import sys
from difflib import SequenceMatcher
from itertools import pairwise
from pathlib import Path

from app.conversation import UNIVERSAL, paths
from app.pronunciation import words

DEFAULT = Path(__file__).parent.parent / "data" / "dialogues_nb.json"
MIN_TURNS = 5
MAX_TURNS = 10
NEAR_SPELLING = 0.6


def near_pair(a: str, b: str) -> str | None:
    """Why two answers are too alike to tell apart by ear, or None."""
    wa, wb = words(a), words(b)
    if wa == wb:
        return "the same words"
    if len(wa) == len(wb):
        diff = [(x, y) for x, y in zip(wa, wb) if x != y]
        if len(diff) == 1:
            x, y = diff[0]
            ratio = SequenceMatcher(None, x, y).ratio()
            if ratio >= NEAR_SPELLING:
                return f"one word apart, {x!r} / {y!r} spelt alike ({ratio:.2f})"
    return None


def _check_slot(where: str, a: dict) -> list[str]:
    """A slot answer ("Jeg heter …") is a pattern over the normalised
    transcript: it must compile, show its slot as "…", and match its own
    example."""
    try:
        pattern = re.compile(a["match"])
    except re.error as exc:
        return [f"{where}: match does not compile ({exc})"]
    problems = []
    if "…" not in a.get("text", ""):
        problems.append(f"{where}: a slot answer's text shows its slot as '…'")
    example = " ".join(words(a.get("example", "")))
    if not example or not pattern.fullmatch(example):
        problems.append(f"{where}: match does not fit its example {a.get('example')!r}")
    return problems


def check_dialogue(d: dict) -> list[str]:
    problems: list[str] = []
    slug = d.get("slug", "?")
    nodes = d.get("nodes", {})
    for key in ("slug", "title", "title_en", "blurb", "level", "character", "start", "nodes"):
        if key not in d:
            problems.append(f"{slug}: missing {key!r}")
    if d.get("start") not in nodes:
        problems.append(f"{slug}: start {d.get('start')!r} is not a node")
        return problems

    universal_ids = {u[0] for u in UNIVERSAL}
    for node_id, node in nodes.items():
        where = f"{slug}/{node_id}"
        for key in ("say", "say_en", "answers"):
            if not node.get(key):
                problems.append(f"{where}: missing {key!r}")
        answers = node.get("answers", [])
        ids = [a.get("id") for a in answers]
        if len(set(ids)) != len(ids):
            problems.append(f"{where}: duplicate answer ids")
        for a in answers:
            for key in ("id", "text", "en", "reply", "reply_en"):
                if not a.get(key):
                    problems.append(f"{where}/{a.get('id')}: missing {key!r}")
            if a.get("id") in universal_ids:
                problems.append(f"{where}/{a['id']}: id is reserved for a universal answer")
            if a.get("match") is not None:
                problems += _check_slot(f"{where}/{a.get('id')}", a)
            if a.get("next") is not None and a["next"] not in nodes:
                problems.append(f"{where}/{a.get('id')}: next {a['next']!r} is not a node")
        texts = [(a.get("id"), a.get("text", "")) for a in answers] + [
            (u[0], u[1]) for u in UNIVERSAL
        ]
        for i, (ia, ta) in enumerate(texts):
            for ib, tb in texts[i + 1 :]:
                why = near_pair(ta, tb)
                if why:
                    problems.append(f"{where}: {ia!r} and {ib!r} are {why}")
    if problems:
        return problems

    try:
        routes = paths(nodes, d["start"])
    except ValueError as exc:
        return [f"{slug}: {exc}"]
    reached = {n for r in routes for n in r}
    for node_id in nodes:
        if node_id not in reached:
            problems.append(f"{slug}/{node_id}: not reachable from the start")
    for r in routes:
        if not MIN_TURNS <= len(r) <= MAX_TURNS:
            problems.append(
                f"{slug}: a path has {len(r)} turns ({' > '.join(r)}); "
                f"want {MIN_TURNS}-{MAX_TURNS}"
            )
    return problems


def check(doc: dict) -> list[str]:
    problems: list[str] = []
    for key in ("unclear", "unclear_en", "choice", "choice_en", "offscript", "offscript_en"):
        if not doc.get("fixed", {}).get(key):
            problems.append(f"fixed: missing {key!r}")
    dialogues = doc.get("dialogues", [])
    slugs = [d.get("slug") for d in dialogues]
    if len(set(slugs)) != len(slugs):
        problems.append("duplicate dialogue slugs")
    levels = [d.get("level", 0) for d in dialogues]
    if any(b <= a for a, b in pairwise(levels)):
        problems.append(f"levels must rise with position, easiest first: {levels}")
    for d in dialogues:
        problems += check_dialogue(d)
    return problems


def unknown_words(doc: dict, known: set[str]) -> dict[str, set[str]]:
    """Per dialogue, words in learner answers that the deck does not hold."""
    out: dict[str, set[str]] = {}
    for d in doc.get("dialogues", []):
        missing = {
            w
            for node in d["nodes"].values()
            for a in node["answers"]
            for w in words(a["text"])
            if w not in known
        }
        if missing:
            out[d["slug"]] = missing
    return out


def deck_words() -> set[str]:
    """Every word form in the deck's headwords and examples."""
    from app.content import merge_local_deck

    known: set[str] = set()
    for entry in merge_local_deck("nb"):
        known.update(words(entry["no"]))
        for ex in entry.get("examples") or ():
            known.update(words(ex["no"]))
    return known


def summary(doc: dict) -> list[str]:
    """One line per dialogue: level, turns per path, mean answer length."""
    lines = []
    for d in doc["dialogues"]:
        routes = paths(d["nodes"], d["start"])
        lengths = sorted({len(r) for r in routes})
        answers = [a for n in d["nodes"].values() for a in n["answers"]]
        mean_words = sum(len(words(a["text"])) for a in answers) / len(answers)
        lines.append(
            f"  {d['level']}. {d['slug']:10} turns {'/'.join(map(str, lengths)):6}"
            f" answers {len(answers):3}  mean answer {mean_words:.1f} words"
        )
    return lines


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    doc = json.loads(path.read_text())
    problems = check(doc)
    for p in problems:
        print("FAIL", p)
    if not problems:
        print("\n".join(summary(doc)))
        for slug, missing in unknown_words(doc, deck_words()).items():
            print(f"  not in the deck ({slug}): {', '.join(sorted(missing))}")
    print(f"{len(problems)} problem(s) in {path.name}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
