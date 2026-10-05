"""Decoding what the browser actually records.

Skipped unless the speech dependencies are installed (`uv sync --group
speech`, or the dedicated venv on the external drive), since the API venv
deliberately carries no ML or codec libraries.
"""

import pytest

av = pytest.importorskip("av", reason="needs the speech dependency group")

def make_webm_opus(seconds: float = 1.0, rate: int = 48_000) -> bytes:
    """A real webm/opus file, the way Chrome and Firefox record."""
    import io
    import math

    import numpy as np

    buf = io.BytesIO()
    with av.open(buf, mode="w", format="webm") as container:
        stream = container.add_stream("libopus", rate=rate)
        stream.layout = "mono"
        n = int(seconds * rate)
        tone = (0.3 * np.sin(2 * math.pi * 440 * np.arange(n) / rate)).astype("float32")
        frame = av.AudioFrame.from_ndarray(tone.reshape(1, -1), format="fltp", layout="mono")
        frame.rate = rate
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buf.getvalue()


def test_decodes_webm_opus_to_16khz_mono():
    # The regression this exists for: librosa 1.0 reads through soundfile
    # alone and answered "Format not recognised" for every real recording,
    # because soundfile cannot open webm/opus at all.
    from speech_service.transcriber import SAMPLE_RATE, decode

    samples = decode(make_webm_opus(seconds=1.0))

    assert samples.ndim == 1
    assert samples.dtype.name == "float32"
    # Resampled from 48 kHz to 16 kHz, so about a second's worth. Opus pads a
    # little at the edges, hence the tolerance rather than an exact count.
    assert abs(len(samples) / SAMPLE_RATE - 1.0) < 0.12


def test_the_tail_of_a_short_utterance_is_not_lost():
    # Every recording here is two or three seconds, so a resampler left
    # unflushed would drop a noticeable slice of the last word.
    from speech_service.transcriber import SAMPLE_RATE, decode

    samples = decode(make_webm_opus(seconds=0.25))
    assert len(samples) / SAMPLE_RATE > 0.2


def test_a_file_with_no_audio_stream_is_rejected_clearly():
    from speech_service.transcriber import decode

    with pytest.raises(Exception):
        decode(b"not a media container at all")
