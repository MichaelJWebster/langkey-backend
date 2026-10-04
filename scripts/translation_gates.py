"""Deterministic checks on a prepared translation.

Cheap, model-free checks over the staging JSON from scripts/prepare_text.py.
They catch structural faults -- dropped content, leaked commentary, archaic
register, print apparatus in the source -- which is what has actually gone
wrong here. They say nothing about whether the English means what the
Norwegian means; that needs a model, and these decide what is worth asking
about.

Each gate is classified by who can fix it:

  TRANSLATION  the Norwegian is fine and the English is wrong, so the
               translator can be asked to try again.
  SOURCE       the English is fine and the Norwegian is corrupt. Retranslating
               cannot help; the fix is upstream in extraction. Report only.

A gate only gets repair enabled once its precision has been measured on a real
corpus. Measured on Mysterier (7228 sentences), auto-repairing a low-precision
gate actively corrupts good translations: the "Norwegian left in the English"
check flags `ten øre`, which is correct, and asking the model to fix it yields
`ten pence`. That gate is report-only for exactly that reason.
"""

import re
from dataclasses import dataclass
from typing import Callable

TRANSLATION = "translation"
SOURCE = "source"

ARCHAIC_RE = re.compile(r"\b(thou|thee|thy|thine|dost|doth|hast|hath|shalt|wilt|ye)\b", re.I)
# Model commentary has to be told apart from first-person narration, which
# this book is full of. Measured on Mysterier, the loose version of this check
# ("^i cannot", "^note:") was wrong 6 times out of 6: `Jeg kan ikke sige` is
# "I cannot say" and `Læg Mærke til:` is "Note:". So only self-referential talk
# about the task counts.
COMMENTARY_RE = re.compile(
    r"^\s*(?:"
    r"here (?:is|are) (?:the |your |a |my )?(?:english|translation)"
    r"|(?:the )?(?:english )?translation(?: of [^:]{0,40})?:"
    r"|(?:sure|certainly|of course|okay)[,!.]?\s*(?:here|i|the)\b"
    r"|as an ai\b"
    r"|i (?:cannot|can't|am unable to|won't|will not) "
    r"(?:translate|provide|assist|comply|fulfil|fulfill|help|do that|complete)"
    r")",
    re.I,
)
# A bracketed note is commentary wherever it appears, not just at the start.
NOTE_RE = re.compile(r"[(\[](?:translator'?s? )?note\b", re.I)
LEADING_NUMBER_RE = re.compile(r"^\s*\d+[.)]\s")
NORWEGIAN_WORD_RE = re.compile(r"\b\w*[æøåÆØÅ]\w*\b")
# A bare number glued to the start of a word or quotation is print pagination
# leaking out of the source. Dano-Norwegian ordinals ("den 12te Juni") look
# the same, so their suffixes are excluded.
GLUED_NUMBER_RE = re.compile(
    r"(?:^|\s)\d+(?!(?:ste|dje|die|den|dre|de|te|nde|re)\b)(?=[A-Za-zÆØÅæøå«\"'(\[])"
)


def _words(s: str) -> int:
    return len(s.split())


@dataclass(frozen=True)
class Gate:
    name: str
    side: str
    summary: str
    check: Callable[[str, str], str | None]
    # How to tell the translator what went wrong. None where no retranslation
    # can help, which is every SOURCE gate, and None where the check is not
    # precise enough to act on without a human looking.
    instruction: str | None = None
    # What this gate scored the last time it was measured against a real
    # corpus. A gate gets an instruction only once this says it is safe.
    measured: str = "not yet measured"

    @property
    def repairable(self) -> bool:
        return self.side == TRANSLATION and self.instruction is not None


def _empty(no: str, en: str) -> str | None:
    return "no translation produced" if not en.strip() else None


def _too_short(no: str, en: str) -> str | None:
    if _words(no) > 8 and _words(en) < 0.45 * _words(no):
        return f"{_words(en)} English words for {_words(no)} Norwegian"
    return None


def _too_long(no: str, en: str) -> str | None:
    if _words(no) > 5 and _words(en) > 2.5 * _words(no):
        return f"{_words(en)} English words for {_words(no)} Norwegian"
    return None


def _archaic(no: str, en: str) -> str | None:
    found = sorted({m.group(0).lower() for m in ARCHAIC_RE.finditer(en)})
    return f"archaic English: {', '.join(found)}" if found else None


def _commentary(no: str, en: str) -> str | None:
    m = COMMENTARY_RE.match(en) or NOTE_RE.search(en)
    return f"model commentary: {m.group(0).strip()!r}" if m else None


def _leading_number(no: str, en: str) -> str | None:
    m = LEADING_NUMBER_RE.match(en)
    return f"list numbering left in: {m.group(0).strip()!r}" if m else None


def _norwegian_left(no: str, en: str) -> str | None:
    words = sorted({w.lower() for w in NORWEGIAN_WORD_RE.findall(en)})
    # One such word is usually a name or a kept loanword (Skål, øre); several
    # suggest a phrase came through untranslated.
    return f"Norwegian words in the English: {', '.join(words)}" if len(words) >= 2 else None


def _glued_number(no: str, en: str) -> str | None:
    m = GLUED_NUMBER_RE.search(no)
    return f"page marker in the Norwegian near {no[max(0, m.start()):m.start() + 18]!r}" if m else None


# Minimum English words before the echo check will speak. Measured on
# Mysterier: with no guard it fires 80 times, every one of them a short line
# that is its own translation ("Pause.", "Sara.", "He-he-he."). At four words
# it fires 0 times on a sound translation and caught the one case where a
# repair handed back the Norwegian untouched.
ECHO_MIN_WORDS = 4
ECHO_OVERLAP = 0.5
_WORD_RE = re.compile(r"[a-zæøåA-ZÆØÅ]+")


def _echo_overlap(no: str, en: str) -> tuple[float, int]:
    no_words = set(_WORD_RE.findall(no.lower()))
    en_words = set(_WORD_RE.findall(en.lower()))
    if not no_words or not en_words:
        return 0.0, len(en_words)
    return len(no_words & en_words) / len(en_words), len(en_words)


def looks_untranslated(no: str, en: str) -> bool:
    """True when the English is mostly the Norwegian handed back."""
    overlap, words = _echo_overlap(no, en)
    return words >= ECHO_MIN_WORDS and overlap > ECHO_OVERLAP


def _echoed_source(no: str, en: str) -> str | None:
    overlap, words = _echo_overlap(no, en)
    if words >= ECHO_MIN_WORDS and overlap > ECHO_OVERLAP:
        return f"{overlap:.0%} of the English words are also in the Norwegian"
    return None


def _digit_mismatch(no: str, en: str) -> str | None:
    missing = sorted(set(re.findall(r"\d+", no)) - set(re.findall(r"\d+", en)))
    return f"numbers in the Norwegian but not the English: {', '.join(missing)}" if missing else None


GATES: tuple[Gate, ...] = (
    Gate(
        "empty", TRANSLATION, "no English at all", _empty,
        "The sentence was left untranslated. Translate it into English.",
        measured="Mysterier: 0 cases, so untested in anger; the check cannot "
        "misfire -- an empty string is empty.",
    ),
    Gate(
        "too_short", TRANSLATION, "English far shorter than the Norwegian", _too_short,
        "Your previous translation dropped most of the sentence. Translate the "
        "whole thing, leaving nothing out.",
        measured="Mysterier: 0 cases at 0.45x. Earlier runs at 0.6x flagged "
        "legitimately terse English, which is why the threshold is this low.",
    ),
    Gate(
        "too_long", TRANSLATION, "English far longer than the Norwegian", _too_long,
        "Your previous translation added material that is not in the Norwegian. "
        "Translate only what the sentence says, adding no explanation.",
        measured="Mysterier: 1 case, genuine (the model echoed the Norwegian "
        "and then translated it). 1/1.",
    ),
    Gate(
        "archaic_english", TRANSLATION, "thou/thee/dost in the English", _archaic,
        "Your previous translation used archaic English (thou, thee, dost, ye). "
        "The Norwegian is 19th-century, but the English must be plain and modern.",
        measured="Mysterier: 5 cases, all genuine, all repaired on the first "
        "attempt. 5/5.",
    ),
    Gate(
        "commentary", TRANSLATION, "model talking rather than translating", _commentary,
        "Your previous reply contained commentary. Reply with the English "
        "translation alone and nothing else.",
        measured="Mysterier: 0 cases once narrowed to task talk. The loose "
        "version scored 0/6 -- see COMMENTARY_RE.",
    ),
    Gate(
        "leading_number", TRANSLATION, "a stray number before the English", _leading_number,
        "Your previous reply began with a number that is not part of the "
        "sentence. Reply with the translation alone, with no leading number.",
        measured="Mysterier: 1 case, genuine (print page number 40 carried into "
        "the English). 1/1.",
    ),
    Gate(
        "echoed_source", TRANSLATION, "the Norwegian handed back untranslated", _echoed_source,
        "Your previous reply was the Norwegian sentence itself, not a "
        "translation. Translate it into English.",
        measured="Mysterier: 0 cases in the translation, 1 in a repair -- the "
        "model answered with the source and the other gates could not see it, "
        "because that sentence happens to contain no æ, ø or å. 1/1 at "
        f"{ECHO_MIN_WORDS} words; see ECHO_MIN_WORDS for the 80 it avoids.",
    ),
    # Report-only below. Either retranslating cannot fix the fault because it
    # is in the Norwegian, or the check is not precise enough to act on.
    Gate(
        "norwegian_left", SOURCE, "untranslated Norwegian in the English", _norwegian_left,
        measured="Mysterier: 28 cases, mixed. Some are whole sentences left "
        "untranslated, but place names (Gjovik) and kept loanwords land here "
        "too, and repairing those turns `ten ore` into `ten pence`. Needs eyes.",
    ),
    Gate(
        "glued_number", SOURCE, "print pagination in the Norwegian", _glued_number,
        measured="Mysterier: 0 after the extraction fix; 433 before it. The fix "
        "belongs in prepare_text.py, so this gate only watches for regressions.",
    ),
    Gate(
        "digit_mismatch", SOURCE, "a number lost between the two", _digit_mismatch,
        measured="Mysterier: 5 cases, all page numbers fused into a word "
        "(`jeg36var`), which the glued-number check cannot see. Source-side.",
    ),
)

BY_NAME = {g.name: g for g in GATES}


@dataclass(frozen=True)
class Finding:
    seq: int
    gate: str
    detail: str
    no: str
    en: str

    @property
    def repairable(self) -> bool:
        return BY_NAME[self.gate].repairable


def check_sentence(seq: int, no: str, en: str, gates: tuple[Gate, ...] = GATES) -> list[Finding]:
    out = []
    for gate in gates:
        detail = gate.check(no, en)
        if detail:
            out.append(Finding(seq, gate.name, detail, no, en))
    return out


def check_all(sentences: list[dict]) -> list[Finding]:
    found = []
    for s in sentences:
        found += check_sentence(s["seq"], s["no"], s.get("en", ""))
    return found
