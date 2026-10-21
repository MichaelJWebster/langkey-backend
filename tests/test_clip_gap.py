"""Leading silence is capped at 0.5 s (app/audio.py, LEADING_GAP_MAX).

Michael: "Many of the reference sound clips have a long empty space at the
beginning. Could we enforce a gap of only 0.5 seconds for clips where the gap
is longer?"
"""

import importlib.util
import io
import math
import sys
import types
import wave
from array import array
from pathlib import Path

from app.audio import CLIP_FORMAT, LEADING_GAP_MAX, audio_object_key, trim_leading_silence
from scripts.trim_clip_gaps import legacy_key, shifted

RATE = 22050


def clip(gap: float, hiss: int = 120, speech: float = 0.6) -> bytes:
    """`gap` seconds at hiss level (about -49 dBFS peak, like Piper's), then a tone."""
    n = int(gap * RATE)
    quiet = [hiss if i % 2 else -hiss for i in range(n)]
    tone = [int(8000 * math.sin(2 * math.pi * 220 * i / RATE)) for i in range(int(speech * RATE))]
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(array("h", quiet + tone).tobytes())
    return out.getvalue()


def gap_of(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        s = array("h", w.readframes(w.getnframes()))
    return next(i for i, x in enumerate(s) if abs(x) > 1000) / RATE


def test_a_long_gap_is_cut_to_half_a_second():
    out, cut = trim_leading_silence(clip(3.2))
    assert abs(gap_of(out) - LEADING_GAP_MAX) <= 0.02  # the onset is found to a 20 ms frame
    assert abs(cut - 2.7) <= 0.02


def test_a_short_gap_is_left_alone():
    data = clip(0.3)
    assert trim_leading_silence(data) == (data, 0.0)


def test_silence_throughout_is_left_alone():
    data = clip(1.0, speech=0)
    assert trim_leading_silence(data) == (data, 0.0)


def test_the_format_is_part_of_the_key():
    # Clips are served immutable: changed bytes need a new URL.
    assert audio_object_key("nb", "Jeg bor i Oslo.", primary=True, scale=3.4) != legacy_key(
        "nb", "Jeg bor i Oslo.", True, 3.4
    )


def test_alignments_move_with_the_cut():
    words = [{"word": "Jeg", "start": 3.1, "end": 3.4}, None]
    assert shifted(words, 2.6) == [{"word": "Jeg", "start": 0.5, "end": 0.8}, None]


def test_the_generator_trims_and_keys_like_the_app(monkeypatch):
    # docker/piper is its own image and cannot import app/, so it carries
    # copies; this keeps them from drifting.
    for name in ("boto3", "duckdb", "piper"):
        monkeypatch.setitem(sys.modules, name, types.SimpleNamespace(client=lambda *a, **k: None,
                                                                     PiperVoice=None, SynthesisConfig=None))
    path = Path(__file__).parent.parent / "docker" / "piper" / "generate_audio.py"
    spec = importlib.util.spec_from_file_location("generate_audio", path)
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)

    assert gen.CLIP_FORMAT == CLIP_FORMAT
    for text, primary, scale in [("Jeg bor i Oslo.", True, 3.4), ("et hus", False, 1.6)]:
        assert gen.audio_object_key("nb", text, primary, scale) == audio_object_key(
            "nb", text, primary=primary, scale=scale
        )
    data = clip(2.0)
    assert gen.trim_leading_silence(data) == trim_leading_silence(data)
