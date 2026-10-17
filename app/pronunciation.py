"""Compare what the learner was asked to say with what the model heard.

This is deliberately not a score. ADR-025's finding is that a speech-to-text
model repairs wobbly speech into fluent Norwegian rather than reporting what
was actually said, so a single transcript cannot grade anyone. What it can do
is say which word the model failed to find, and what it put there instead --
and across enough attempts that stops being noise. Measured on 2026-10-06,
`klokka` failed in 68% of 22 attempts while fourteen other words never failed
once. One miss is nothing; fifteen out of twenty-two is a thing to practise.

So: per-take marks are a hint, the running tally is the signal, and neither is
ever presented as a mark out of anything.
"""

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

# Enough attempts that a word's record means something. Below this the UI
# shows the attempts without drawing a conclusion from them.
PATTERN_MIN_ATTEMPTS = 4
# At or below this hit rate a word is worth practising.
PATTERN_MAX_HIT_RATE = 0.6

SAID = "said"        # the model found this word
SLIPPED = "slipped"  # it found something close -- a vowel or ending adrift
MISSED = "missed"    # nothing of it came through

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def normalise(text: str) -> str:
    """Lower-case, strip punctuation, collapse whitespace."""
    return " ".join(_PUNCT.sub(" ", text.lower()).split())


def words(text: str) -> list[str]:
    return normalise(text).split()


def _fold(word: str) -> str:
    """Drop the diacritics, so klokka/klokke differ but hår/har do not.

    æ ø å carry meaning in Norwegian, but a transcript that only differs by
    one is a near miss rather than a failure -- the same judgement the written
    exercises already make (ADR-019).
    """
    decomposed = unicodedata.normalize("NFD", word)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.replace("æ", "ae").replace("ø", "o").replace("å", "a")


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


@dataclass(frozen=True)
class WordMark:
    """One target word, and what became of it."""

    word: str  # as written in the target, punctuation and case intact
    verdict: str
    heard: str | None = None  # what the model put in its place
    # Index into `words(heard)` of the word this mark came from -- the one
    # the model found here, right or wrong. None when nothing was found, so
    # there is no word of the take to point at.
    heard_index: int | None = None


Span = tuple[float, float]


def compare(target: str, heard: str) -> list[WordMark]:
    """Line the transcript up against the target, word by word.

    Alignment rather than set membership: knowing that `klokka` came back as
    `klokke` is the whole value, and a bag of words only says it is absent.
    """
    original = target.split()  # keeps punctuation for display
    want = words(target)
    got = words(heard)
    if not want:
        return []

    # original and want can differ in length when a token is pure punctuation.
    display = original if len(original) == len(want) else want

    marks: list[WordMark | None] = [None] * len(want)
    for op, i1, i2, j1, j2 in SequenceMatcher(None, want, got).get_opcodes():
        if op == "equal":
            for offset, k in enumerate(range(i1, i2)):
                marks[k] = WordMark(display[k], SAID, heard_index=j1 + offset)
        elif op == "delete":
            for k in range(i1, i2):
                marks[k] = WordMark(display[k], MISSED)
        elif op == "replace":
            for offset, k in enumerate(range(i1, i2)):
                j = j1 + offset
                alternative = got[j] if j < j2 else None
                if alternative is None:
                    marks[k] = WordMark(display[k], MISSED)
                elif _fold(want[k]) == _fold(alternative) or _similar(want[k], alternative) >= 0.7:
                    marks[k] = WordMark(display[k], SLIPPED, alternative, heard_index=j)
                else:
                    marks[k] = WordMark(display[k], MISSED, alternative, heard_index=j)
        # "insert" is a word the model added; it belongs to no target word.

    return [m or WordMark(display[k], MISSED) for k, m in enumerate(marks)]


def take_spans(marks: list[WordMark], heard_spans: list[Span | None]) -> list[Span | None]:
    """Where in the take to look for each target word.

    `heard_spans` places each word of the transcript (ADR-043). A target word
    the model found -- right or wrong -- is wherever that heard word is: for
    `klokka` heard as `klokke`, the stretch of the take that *was* klokke is
    exactly what the learner should hear back. A target word the model found
    nothing for has no word of its own, so it gets the stretch between its
    neighbours' words where it should have been; with no neighbour on one
    side there is nothing to point at, and the entry is None.
    """
    placed: list[Span | None] = [
        heard_spans[m.heard_index]
        if m.heard_index is not None and m.heard_index < len(heard_spans)
        else None
        for m in marks
    ]

    out: list[Span | None] = []
    for k, (m, span) in enumerate(zip(marks, placed)):
        if span is not None:
            out.append(span)
            continue
        before = next((p for p in reversed(placed[:k]) if p is not None), None)
        after = next((p for p in placed[k + 1 :] if p is not None), None)
        if before is not None and after is not None and after[0] > before[1]:
            out.append((before[1], after[0]))
        else:
            out.append(None)
    return out


def is_pattern(hits: int, attempts: int) -> bool:
    """Has this word failed often enough, and enough times, to be worth saying?"""
    if attempts < PATTERN_MIN_ATTEMPTS:
        return False
    return hits / attempts <= PATTERN_MAX_HIT_RATE
