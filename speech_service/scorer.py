"""How well a take supports each of several candidate texts.

The conversation turn's recogniser (ROADMAP M3 step 5). A turn knows roughly
what the learner will say, so rather than ask "what was said?" -- which
NB-Whisper answers with fluent Norwegian the learner may not have produced --
it asks "which of these does the audio support best?". The answer is the CTC
log-likelihood log P(text | audio) under the aligner's wav2vec2 model, summed
over every alignment, reported with the candidate's letter count so the caller
can compare per letter: the raw sum favours shorter texts (on 506 takes the
target beat Whisper's transcript 86 times when it was the shorter text and 55
when longer).

Measured in scripts/bench_context.py, which imports this rather than keeping
its own copy, so the benchmark and the learner's turn run the same code.

Shares the aligner's model and processor (`aligner.load`): no second set of
weights, and it runs on the CPU like the aligner, ~0.16 s for a handful of
candidates against a four-second take.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

from speech_service.config import SpeechSettings

# The same tokenisation as app/pronunciation.words, copied rather than
# imported: the speech image carries speech_service/ only (ADR-032), and the
# two must agree, which tests/test_conversation.py checks.
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def words(text: str) -> list[str]:
    return " ".join(_PUNCT.sub(" ", text.lower()).split()).split()

#: (score, letters) per text. score is None where the text has no letters the
#: model knows (digits, a stray symbol) or more letters than the audio has
#: frames -- a forced path through audio too short for the text is fiction.
Scores = list[tuple[float | None, int]]


def make_scorer(settings: SpeechSettings) -> Callable[[object, list[str]], Scores]:
    """A function scoring texts against already-decoded 16 kHz samples."""
    import torch

    from speech_service import aligner, transcriber

    ctc, processor = aligner.load(settings)
    vocab = processor.tokenizer.get_vocab()
    blank = processor.tokenizer.pad_token_id
    delimiter = vocab.get("|")

    def scores(samples, texts: list[str]) -> Scores:
        with torch.no_grad():
            inputs = processor(samples, sampling_rate=transcriber.SAMPLE_RATE, return_tensors="pt")
            logp = torch.log_softmax(ctc(inputs.input_values).logits[0].float(), dim=-1)
        frames = logp.shape[0]
        out: Scores = []
        for t in texts:
            ids, _ = aligner._tokens(words(t), vocab, delimiter)
            if not ids or len(ids) > frames:
                out.append((None, len(ids)))
                continue
            loss = torch.nn.functional.ctc_loss(
                logp.unsqueeze(1), torch.tensor([ids]), torch.tensor([frames]),
                torch.tensor([len(ids)]), blank=blank, reduction="sum",
            )
            out.append((-float(loss), len(ids)))
        return out

    return scores


_scorer: Callable[[object, list[str]], Scores] | None = None


def _cached(settings: SpeechSettings):
    """Built once per process, on the aligner's already-loaded model."""
    global _scorer
    if _scorer is None:
        _scorer = make_scorer(settings)
    return _scorer


def score(audio: bytes, texts: list[str], settings: SpeechSettings) -> dict:
    """The service entry point: decode the browser's audio, score each text."""
    from speech_service.transcriber import decode

    samples = decode(audio)
    started = time.monotonic()
    scored = _cached(settings)(samples, texts)
    return {
        "candidates": [
            {"text": t, "ctc": s, "letters": k} for t, (s, k) in zip(texts, scored)
        ],
        "model": settings.aligner,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
