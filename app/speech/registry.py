"""Pick the transcriber from configuration, and hold ADR-031 to account.

ADR-031 is a firm decision: every model in the learner's path is trained or
fine-tuned for Norwegian, wherever it runs. A decision that lives only in a
document drifts, so it is checked here at startup -- outside dev, configuring
a model that is not known to be Norwegian-trained stops the app rather than
quietly costing a third of the accuracy on Bokmål.
"""

import logging
import re
from functools import lru_cache

from app.config import Settings, get_settings
from app.speech.base import SpeechToText
from app.speech.http import HttpTranscriber
from app.speech.null import NullTranscriber

log = logging.getLogger(__name__)

# Models known to be trained or fine-tuned for Norwegian. NB-Whisper is the
# National Library of Norway's retrain on 66,000 hours (ADR-024); NbAiLab is
# the publisher, so the whole family qualifies. Add to this list only with a
# source for the claim.
NORWEGIAN_MODEL_PATTERNS = (
    re.compile(r"\bnb-whisper\b", re.I),
    re.compile(r"\bnb-wav2vec2?\b", re.I),
    re.compile(r"^nbailab/", re.I),
    # Språkbanken's Bokmål forced aligner, for pronunciation work (ADR-025).
    re.compile(r"\bnofa\b", re.I),
)

# Generic multilingual models, named so the error can say what is wrong rather
# than only that something is. These are the ones easy to reach for by
# accident -- whisper-large-v3-turbo is one Workers AI binding away.
GENERIC_MODEL_PATTERNS = (
    re.compile(r"^(openai/)?whisper[-_]", re.I),
    re.compile(r"\bwhisper-(tiny|base|small|medium|large)", re.I),
)


def is_norwegian_model(model: str) -> bool:
    return any(p.search(model) for p in NORWEGIAN_MODEL_PATTERNS)


def looks_generic(model: str) -> bool:
    return any(p.search(model) for p in GENERIC_MODEL_PATTERNS) and not is_norwegian_model(model)


def check_model_policy(model: str, *, environment: str) -> None:
    """Enforce ADR-031. Raises outside dev; warns in dev so trials stay cheap."""
    if is_norwegian_model(model):
        return
    detail = (
        f"{model!r} is a generic multilingual model"
        if looks_generic(model)
        else f"{model!r} is not a known Norwegian-trained model"
    )
    message = (
        f"ADR-031: speech models must be Norwegian-trained. {detail}. "
        "Generic Whisper scores WER 10.4 against NB-Whisper's 6.6 on Fleurs and "
        "6.8 against 2.2 on NST. Use an nb-whisper model, or add this one to "
        "NORWEGIAN_MODEL_PATTERNS with a source for the claim."
    )
    if environment == "dev":
        log.warning("%s (allowed in dev)", message)
        return
    raise RuntimeError(message)


def build_transcriber(settings: Settings) -> SpeechToText:
    backend = settings.stt_backend.strip().lower()
    if backend in ("", "none", "off"):
        return NullTranscriber()
    if backend == "http":
        if not settings.stt_url:
            raise RuntimeError("LANGKEY_STT_BACKEND=http needs LANGKEY_STT_URL")
        check_model_policy(settings.stt_model, environment=settings.environment)
        return HttpTranscriber(
            settings.stt_url,
            model=settings.stt_model,
            timeout_seconds=settings.stt_timeout_seconds,
        )
    raise RuntimeError(
        f"Unknown LANGKEY_STT_BACKEND {settings.stt_backend!r} (expected 'none' or 'http')"
    )


@lru_cache
def get_transcriber() -> SpeechToText:
    """FastAPI dependency. Cached: an HTTP client is cheap but not free."""
    return build_transcriber(get_settings())
