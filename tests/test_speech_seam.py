"""The speech-to-text seam (ADR-032) and the Norwegian-model policy (ADR-031).

No model runs here. The point of the seam is that the app depends on a
protocol, so these cover the contract, the configuration that chooses an
implementation, and how failures are classified -- unavailable (the service is
down, nothing is the learner's fault) versus failed (this audio was rejected).
"""

import httpx
import pytest

from app.config import Settings
from app.speech import SpeechToText, Transcript, TranscriptionError, TranscriptionUnavailable
from app.speech.http import HttpTranscriber
from app.speech.null import NullTranscriber
from app.speech.registry import build_transcriber, check_model_policy, is_norwegian_model


# --- ADR-031: Norwegian-trained models, local or deployed -------------------


@pytest.mark.parametrize(
    "model",
    [
        "NbAiLab/nb-whisper-small",
        "NbAiLab/nb-whisper-large",
        "nb-whisper-medium",
        "nb-wav2vec2-1b-bokmaal",
        "NoFA",
    ],
)
def test_norwegian_models_are_accepted_anywhere(model):
    assert is_norwegian_model(model)
    check_model_policy(model, environment="prod")


@pytest.mark.parametrize(
    "model",
    [
        # One Workers AI binding away, and the easiest mistake to make.
        "whisper-large-v3-turbo",
        "openai/whisper-small",
        "whisper-medium",
        "some-multilingual-asr",
    ],
)
def test_generic_models_are_refused_outside_dev(model):
    # Not a style preference: generic Whisper scores WER 10.4 against
    # NB-Whisper's 6.6 on Fleurs and 6.8 against 2.2 on NST.
    with pytest.raises(RuntimeError, match="ADR-031"):
        check_model_policy(model, environment="prod")


def test_a_generic_model_is_allowed_in_dev_so_trials_stay_cheap(caplog):
    check_model_policy("whisper-small", environment="dev")
    assert "ADR-031" in caplog.text


def test_the_refusal_says_which_fault_it_found():
    with pytest.raises(RuntimeError, match="generic multilingual model"):
        check_model_policy("openai/whisper-base", environment="prod")
    with pytest.raises(RuntimeError, match="not a known Norwegian-trained model"):
        check_model_policy("mystery-asr-v2", environment="prod")


# --- choosing an implementation --------------------------------------------


def test_no_backend_configured_gives_the_null_transcriber():
    assert build_transcriber(Settings()).name == "none"


@pytest.mark.anyio
async def test_the_null_transcriber_reports_unavailable_not_silence():
    # Silence would make a say-it drill mark everything wrong; unavailable is
    # a 503 the learner can understand.
    with pytest.raises(TranscriptionUnavailable):
        await NullTranscriber().transcribe(b"x", content_type="audio/webm")


def test_http_backend_needs_a_url():
    with pytest.raises(RuntimeError, match="LANGKEY_STT_URL"):
        build_transcriber(Settings(stt_backend="http"))


def test_http_backend_refuses_a_generic_model_before_it_is_ever_called():
    with pytest.raises(RuntimeError, match="ADR-031"):
        build_transcriber(
            Settings(
                stt_backend="http",
                stt_url="http://speech:8001",
                stt_model="whisper-large-v3-turbo",
                environment="prod",
            )
        )


def test_an_unknown_backend_fails_loudly():
    with pytest.raises(RuntimeError, match="Unknown LANGKEY_STT_BACKEND"):
        build_transcriber(Settings(stt_backend="magic"))


def test_both_implementations_satisfy_the_protocol():
    assert isinstance(NullTranscriber(), SpeechToText)
    assert isinstance(HttpTranscriber("http://x", model="nb-whisper-small"), SpeechToText)


# --- the HTTP client's failure classification ------------------------------


@pytest.fixture
def speech_service(monkeypatch):
    """Stand in for the speech container, answering however the test says."""

    def install(handler):
        t = HttpTranscriber("http://speech:8001", model="nb-whisper-small", timeout_seconds=1.0)
        original = httpx.AsyncClient

        def patched(*args, **kwargs):
            kwargs.setdefault("transport", httpx.MockTransport(handler))
            return original(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", patched)
        return t

    return install


@pytest.mark.anyio
async def test_a_good_reply_becomes_a_transcript(speech_service):
    t = speech_service(
        lambda request: httpx.Response(
            200,
            json={
                "text": "Jeg bor i Oslo.",
                "language": "no",
                "model": "NbAiLab/nb-whisper-small",
                "duration_ms": 412,
                "segments": ["Jeg bor i Oslo."],
            },
        )
    )

    result = await t.transcribe(b"audio", content_type="audio/webm")

    assert result == Transcript(
        text="Jeg bor i Oslo.",
        language="no",
        model="NbAiLab/nb-whisper-small",
        duration_ms=412,
        confidence=None,
        segments=("Jeg bor i Oslo.",),
    )


@pytest.mark.anyio
async def test_the_audio_is_sent_as_multipart_with_its_own_content_type(speech_service):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "ja", "language": "no", "model": "m"})

    t = speech_service(handler)
    await t.transcribe(b"OPUSDATA", content_type="audio/webm;codecs=opus")

    assert seen["url"] == "http://speech:8001/transcribe"
    assert b"OPUSDATA" in seen["body"]
    assert b"audio/webm;codecs=opus" in seen["body"]


@pytest.mark.anyio
async def test_a_server_error_is_unavailable_not_a_failed_attempt(speech_service):
    # The container fell over or is still loading the model. Nothing about
    # this is the learner's pronunciation.
    t = speech_service(lambda request: httpx.Response(503))
    with pytest.raises(TranscriptionUnavailable):
        await t.transcribe(b"x", content_type="audio/webm")


@pytest.mark.anyio
async def test_a_rejected_recording_is_a_failure_not_unavailability(speech_service):
    # 4xx is about this audio -- wrong format, too long -- so it must not be
    # reported as the service being down.
    t = speech_service(lambda request: httpx.Response(413))
    with pytest.raises(TranscriptionError) as exc:
        await t.transcribe(b"x", content_type="audio/webm")
    assert not isinstance(exc.value, TranscriptionUnavailable)


@pytest.mark.anyio
async def test_an_unreachable_service_is_unavailable(speech_service):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    t = speech_service(refuse)
    with pytest.raises(TranscriptionUnavailable, match="unreachable"):
        await t.transcribe(b"x", content_type="audio/webm")


@pytest.mark.anyio
async def test_a_timeout_is_unavailable_not_a_bad_recording(speech_service):
    # A cold container loading a multi-gigabyte model can genuinely exceed the
    # timeout, and that must not be scored against the learner.
    def stall(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    t = speech_service(stall)
    with pytest.raises(TranscriptionUnavailable, match="in time"):
        await t.transcribe(b"x", content_type="audio/webm")


@pytest.mark.anyio
async def test_an_unreadable_reply_is_a_failure(speech_service):
    t = speech_service(lambda request: httpx.Response(200, text="not json"))
    with pytest.raises(TranscriptionError, match="unreadable"):
        await t.transcribe(b"x", content_type="audio/webm")


@pytest.mark.anyio
async def test_a_reply_without_text_is_a_failure(speech_service):
    t = speech_service(lambda request: httpx.Response(200, json={"language": "no"}))
    with pytest.raises(TranscriptionError):
        await t.transcribe(b"x", content_type="audio/webm")
