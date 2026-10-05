"""Settings for the speech service, kept separate from the API's."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class SpeechSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LANGKEY_SPEECH_", extra="ignore")

    # ADR-031: Norwegian-trained, wherever this runs. The API refuses a
    # generic model at startup; this default is the small NB-Whisper, which
    # the CPU benchmark in ADR-024 was measured on.
    model: str = "NbAiLab/nb-whisper-small"
    device: str = "auto"  # "auto" | "cpu" | "mps" | "cuda"
    compute_type: str = "int8"
    # Load at startup rather than on the first request. Off by default so the
    # service starts fast in development and in tests.
    preload: bool = False


@lru_cache
def get_speech_settings() -> SpeechSettings:
    return SpeechSettings()
