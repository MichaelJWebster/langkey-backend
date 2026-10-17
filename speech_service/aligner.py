"""Where each word sits in a clip: forced alignment with a Norwegian CTC model.

The point is to play one word of the reference against the same word of the
learner's take, cut from the sentence as actually spoken -- Michael: *"the
pronunciation of a single word can differ from how it's pronounced within the
sentence"* -- so the boundaries have to be real. Whisper's own word
timestamps are not: measured 2026-10-17 on two references and two takes,
most of its boundaries fell mid-speech and every trailing silence was glued
onto the word before it (a 1.5 s "kveld"). A CTC model given the text finds
the letters themselves, to a 20 ms frame.

Two steps. The Viterbi pass below puts each letter on a frame. CTC emissions
are spikes, so the letters of a word cover only part of it (a 0.02 s "i");
`refine` then grows each word outwards through voiced audio to the silence
either side, and where two words run together with no silence between them,
splits at the midpoint of the gap between their letters. Validated against
the measured silences in those four clips: every real gap between words lands
on a boundary, and the only silences left inside a word are the stop
closures in vin-t-eren and mar-k-a, which belong there.

Written out rather than taken from torchaudio, which the speech venv does
not carry; the trellis is forty lines and these are four-second clips.
"""

from __future__ import annotations

import logging
import time
import unicodedata
from typing import TYPE_CHECKING

from speech_service.config import SpeechSettings
from speech_service.transcriber import SAMPLE_RATE, decode

if TYPE_CHECKING:
    import numpy as np

log = logging.getLogger(__name__)

# Below this, relative to the loudest frame, a frame is silence. The same
# 30 dB the onset check uses, and comfortably above the mic hiss on the takes.
SILENCE_DB = -30.0
# How far a word may grow into the lead-in or tail. A breath sits under the
# floor anyway; this is a backstop against a noisy room.
MAX_GROW_FRAMES = 20  # 0.4 s at 20 ms

_model = None
_processor = None
_loaded_model: str | None = None


def is_loaded() -> bool:
    return _model is not None


def loaded_model() -> str | None:
    return _loaded_model


def load(settings: SpeechSettings):
    """Build the model once. On the CPU: a clip takes 0.2-0.3 s there, which
    is not worth a second Metal context beside Whisper's."""
    global _model, _processor, _loaded_model
    if _model is not None:
        return _model, _processor

    from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

    log.info("loading aligner %s", settings.aligner)
    started = time.monotonic()
    _processor = Wav2Vec2Processor.from_pretrained(settings.aligner)
    _model = Wav2Vec2ForCTC.from_pretrained(settings.aligner).eval()
    _loaded_model = settings.aligner
    log.info("loaded aligner in %.1fs", time.monotonic() - started)
    return _model, _processor


# --- the trellis --------------------------------------------------------------


def viterbi(logp: np.ndarray, tokens: list[int], blank: int) -> list[int]:
    """Best CTC path for `tokens` through `logp` (frames x vocab, log-probs).

    Returns, per frame, the index into `tokens` of the letter on that frame,
    or -1 for a blank. Standard CTC: blanks interleaved between letters, a
    letter may stay, advance from the blank before it, or skip that blank
    when the previous letter differs. Vectorised across states; the loop is
    over frames only.
    """
    import numpy as np

    T = logp.shape[0]
    ext = [blank]
    for t in tokens:
        ext += [t, blank]
    S = len(ext)
    ext_arr = np.asarray(ext)
    emit = logp[:, ext_arr]  # (T, S)
    neg = -1e30

    skip_ok = np.zeros(S, dtype=bool)
    for s in range(2, S):
        skip_ok[s] = ext[s] != blank and ext[s] != ext[s - 2]

    dp = np.full(S, neg)
    dp[0] = emit[0, 0]
    if S > 1:
        dp[1] = emit[0, 1]
    back = np.zeros((T, S), dtype=np.int32)
    states = np.arange(S)
    for t in range(1, T):
        prev1 = np.concatenate(([neg], dp[:-1]))
        prev2 = np.where(skip_ok, np.concatenate(([neg, neg], dp[:-2])), neg)
        stacked = np.stack([dp, prev1, prev2])
        arg = stacked.argmax(axis=0)
        back[t] = states - arg
        dp = stacked[arg, states] + emit[t]

    s = S - 1 if S < 2 or dp[S - 1] >= dp[S - 2] else S - 2
    path = np.empty(T, dtype=np.int32)
    for t in range(T - 1, -1, -1):
        path[t] = s
        s = back[t, s]
    return [-1 if ext[p] == blank else (p - 1) // 2 for p in path]


def letter_spans(path: list[int], groups: list[tuple[int, int] | None]) -> list[tuple[int, int] | None]:
    """First and last frame of each word's letters. `groups` gives each word's
    first and last token index, or None for a word with no alignable letters."""
    first: dict[int, int] = {}
    last: dict[int, int] = {}
    for frame, token in enumerate(path):
        if token < 0:
            continue
        first.setdefault(token, frame)
        last[token] = frame
    out: list[tuple[int, int] | None] = []
    for group in groups:
        if group is None or group[0] not in first or group[1] not in last:
            out.append(None)
        else:
            out.append((first[group[0]], last[group[1]]))
    return out


def refine(
    raw: list[tuple[int, int] | None], rms: np.ndarray, floor: float
) -> list[tuple[int, int] | None]:
    """Grow each word's letter span out to the silence either side.

    A word may not grow past its neighbours' letters. Where two words touch
    with no silence between them both walks meet inside voiced audio, and the
    boundary goes at the midpoint between the last letter of one and the
    first letter of the next -- the best a boundary can do when the speaker
    did not pause.
    """
    n = len(rms)
    quiet = rms < floor  # numpy array; no import needed here
    out: list[tuple[int, int] | None] = [None] * len(raw)
    known = [i for i, r in enumerate(raw) if r is not None]
    for pos, i in enumerate(known):
        a, b = raw[i]
        lo = raw[known[pos - 1]][1] + 1 if pos > 0 else max(0, a - MAX_GROW_FRAMES)
        hi = raw[known[pos + 1]][0] if pos + 1 < len(known) else min(n, b + 1 + MAX_GROW_FRAMES)
        s = a
        while s > lo and not quiet[s - 1]:
            s -= 1
        e = b + 1
        while e < hi and not quiet[e]:
            e += 1
        out[i] = (s, e)
    for pos in range(len(known) - 1):
        i, j = known[pos], known[pos + 1]
        if out[i][1] > out[j][0]:
            mid = (raw[i][1] + 1 + raw[j][0]) // 2
            out[i] = (out[i][0], mid)
            out[j] = (mid, out[j][1])
    return out


def envelope(samples: np.ndarray, frames: int) -> np.ndarray:
    """RMS per CTC frame, so the energy and the letters share a clock."""
    import numpy as np

    hop = max(1, len(samples) // frames)
    return np.array(
        [float(np.sqrt(np.mean(samples[i * hop : (i + 1) * hop] ** 2))) for i in range(frames)]
    )


# --- the service entry point ---------------------------------------------------


def _tokens(words: list[str], vocab: dict[str, int], delimiter: int | None):
    """Letters of every word as token ids, with the model's word delimiter
    between words. A word with no letters in the vocabulary (digits, a stray
    symbol) gets no group and comes back unaligned rather than failing the
    sentence."""
    ids: list[int] = []
    groups: list[tuple[int, int] | None] = []
    for word in words:
        letters = [vocab[c] for c in unicodedata.normalize("NFC", word.lower()) if c in vocab]
        if not letters:
            groups.append(None)
            continue
        if ids and delimiter is not None:
            ids.append(delimiter)
        groups.append((len(ids), len(ids) + len(letters) - 1))
        ids.extend(letters)
    return ids, groups


def align(audio: bytes, words: list[str], settings: SpeechSettings) -> dict:
    """Where each of `words` is in `audio`, in seconds. `words` must be the
    text as spoken, already split and stripped of punctuation -- the caller
    owns tokenisation, so that its word positions and these line up."""
    import torch

    model, processor = load(settings)
    samples = decode(audio)
    duration = len(samples) / SAMPLE_RATE
    started = time.monotonic()

    vocab = processor.tokenizer.get_vocab()
    blank = processor.tokenizer.pad_token_id
    delimiter = vocab.get("|")
    ids, groups = _tokens(words, vocab, delimiter)

    spans: list[tuple[int, int] | None] = [None] * len(words)
    frames = 0
    if ids:
        with torch.no_grad():
            inputs = processor(samples, sampling_rate=SAMPLE_RATE, return_tensors="pt")
            logits = model(inputs.input_values).logits[0]
        logp = torch.log_softmax(logits.float(), dim=-1).numpy()
        frames = logp.shape[0]
        # A CTC path needs at least a frame per letter; shorter audio than
        # that cannot carry the text, and a forced path through it would be
        # fiction.
        if frames >= len(ids):
            path = viterbi(logp, ids, blank)
            rms = envelope(samples, frames)
            floor = float(rms.max()) * 10 ** (SILENCE_DB / 20)
            spans = refine(letter_spans(path, groups), rms, floor)

    per_frame = duration / frames if frames else 0.0
    return {
        "words": [
            {
                "word": w,
                "start": round(span[0] * per_frame, 3),
                "end": round(span[1] * per_frame, 3),
            }
            if span is not None
            else None
            for w, span in zip(words, spans)
        ],
        "model": settings.aligner,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
