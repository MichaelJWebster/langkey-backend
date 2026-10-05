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


def decode(audio: bytes) -> tuple["object", int]:
    """Browser audio -> 16 kHz mono float array, which is what Whisper wants.

    MediaRecorder gives webm/opus on Chrome and Firefox and mp4 on Safari, so
    the container cannot be assumed. soundfile handles what it can and ffmpeg
    (via torchaudio's backend) the rest; callers never have to care, which is
    the contract app/speech/base.py promises.
    """
    import librosa

    # librosa goes through audioread/ffmpeg for compressed containers and
    # resamples in one step.
    samples, rate = librosa.load(io.BytesIO(audio), sr=16_000, mono=True)
    return samples, rate


def transcribe(audio: bytes, settings: SpeechSettings, language: str = "no") -> dict:
    asr = load(settings)
    samples, _ = decode(audio)
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
