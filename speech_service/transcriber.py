"""Load NB-Whisper and transcribe one utterance.

torch and transformers are imported lazily, inside the functions that need
them, so this module can be imported — and the service's routing tested —
without several gigabytes of dependencies present. That also keeps the API
image free of them (ADR-032).
"""

import io
import logging
import time

from speech_service.config import SpeechSettings

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000

_pipeline = None
_loaded_model: str | None = None


def resolve_device(requested: str) -> str:
    """Pick a device, preferring Metal on this machine and never guessing CUDA."""
    if requested != "auto":
        return requested
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def is_loaded() -> bool:
    return _pipeline is not None


def loaded_model() -> str | None:
    return _loaded_model


def load(settings: SpeechSettings):
    """Build the pipeline once. Called on first use, or at startup if preload."""
    global _pipeline, _loaded_model
    if _pipeline is not None:
        return _pipeline

    import torch
    from transformers import pipeline

    device = resolve_device(settings.device)
    # float16 on Metal and CUDA, float32 on CPU: half precision on CPU is
    # slower, not faster.
    dtype = torch.float16 if device in ("mps", "cuda") else torch.float32
    log.info("loading %s on %s (%s)", settings.model, device, dtype)
    started = time.monotonic()
    _pipeline = pipeline(
        "automatic-speech-recognition",
        model=settings.model,
        device=device,
        torch_dtype=dtype,
    )
    _loaded_model = settings.model
    log.info("loaded %s in %.1fs", settings.model, time.monotonic() - started)
    return _pipeline


def decode(audio: bytes) -> "object":
    """Browser audio -> 16 kHz mono float32, which is what Whisper wants.

    MediaRecorder gives webm/opus on Chrome and Firefox and mp4/aac on Safari,
    so the container cannot be assumed; callers never have to care, which is
    the contract app/speech/base.py promises.

    PyAV rather than librosa or soundfile. librosa 1.0 dropped its
    audioread/ffmpeg fallback and reads through soundfile alone, which cannot
    open webm/opus at all -- every one of the first real recordings came back
    "Format not recognised". PyAV bundles the ffmpeg libraries in its wheel,
    so this needs no system ffmpeg on the Mac and no apt package in the image.
    """
    import av
    import numpy as np

    with av.open(io.BytesIO(audio)) as container:
        if not container.streams.audio:
            raise ValueError("No audio stream in the recording")
        stream = container.streams.audio[0]
        # Whisper wants 16 kHz mono float; resampling here means the model
        # never sees whatever the browser happened to capture at.
        resampler = av.audio.resampler.AudioResampler(
            format="fltp", layout="mono", rate=SAMPLE_RATE
        )
        chunks: list = []
        for frame in container.decode(stream):
            for resampled in resampler.resample(frame):
                chunks.append(resampled.to_ndarray().reshape(-1))
        # Flush whatever the resampler is still holding, or the tail of short
        # utterances goes missing -- and these are all short utterances.
        for resampled in resampler.resample(None):
            chunks.append(resampled.to_ndarray().reshape(-1))

    if not chunks:
        raise ValueError("The recording decoded to no audio")
    return np.concatenate(chunks).astype("float32")


def transcribe(audio: bytes, settings: SpeechSettings, language: str = "no") -> dict:
    asr = load(settings)
    samples = decode(audio)
    started = time.monotonic()
    result = asr(
        samples,
        return_timestamps=False,
        generate_kwargs={"language": language, "task": "transcribe"},
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)

    text = (result.get("text") or "").strip()
    return {
        "text": text,
        "language": language,
        "model": settings.model,
        "duration_ms": elapsed_ms,
        "confidence": None,  # the HF pipeline does not surface logprobs
        "segments": [c["text"].strip() for c in result.get("chunks") or [] if c.get("text")],
    }
