"""The speech-to-text seam (ADR-032) and the Norwegian-model policy (ADR-031).

No model runs here. The point of the seam is that the app depends on a
protocol, so these cover the contract, the configuration that chooses an
implementation, and how failures are classified -- unavailable (the service is
down, nothing is the learner's fault) versus failed (this audio was rejected).
"""

import httpx
import pytest

from app.config import Settings
from app.speech import (
    SpeechToText,
    Transcript,
    TranscriptionError,
    TranscriptionUnavailable,
    WordAligner,
    WordSpan,
)
from app.speech.http import HttpTranscriber
from app.speech.null import NullTranscriber
from app.speech.registry import (
    build_transcriber,
    check_model_policy,
    get_aligner,
    is_norwegian_model,
)


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


# --- word alignment over the same seam (ADR-043) ------------------------------


@pytest.mark.anyio
async def test_a_good_alignment_reply_places_every_word(speech_service):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/align"
        return httpx.Response(
            200,
            json={
                "words": [{"word": "god", "start": 0.16, "end": 0.64}, None],
                "model": "NbAiLab/nb-wav2vec2-300m-bokmaal",
            },
        )

    t = speech_service(handler)
    alignment = await t.align(b"wav", content_type="audio/wav", words=["god", "kveld"])

    assert alignment.words[0] == WordSpan("god", 0.16, 0.64)
    assert alignment.words[1] is None  # the service could not place it; not an error
    assert "wav2vec2" in alignment.model


@pytest.mark.anyio
async def test_the_words_travel_with_the_audio_as_json(speech_service):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read()
        return httpx.Response(200, json={"words": [None], "model": "m"})

    t = speech_service(handler)
    await t.align(b"wav", content_type="audio/wav", words=["klokka"])

    assert b'name="words"' in seen["body"]
    assert b'["klokka"]' in seen["body"]
    assert b"audio/wav" in seen["body"]


@pytest.mark.anyio
async def test_a_reply_with_the_wrong_number_of_spans_is_a_failure(speech_service):
    # One entry per word sent is the contract; anything else cannot be
    # lined up with the marks and must not be guessed at.
    t = speech_service(lambda r: httpx.Response(200, json={"words": [], "model": "m"}))
    with pytest.raises(TranscriptionError):
        await t.align(b"wav", content_type="audio/wav", words=["a", "b"])


@pytest.mark.anyio
async def test_a_server_error_on_align_is_unavailable(speech_service):
    t = speech_service(lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(TranscriptionUnavailable):
        await t.align(b"wav", content_type="audio/wav", words=["a"])


@pytest.mark.anyio
async def test_the_null_backend_cannot_align_either():
    with pytest.raises(TranscriptionUnavailable):
        await NullTranscriber().align(b"x", content_type="audio/wav", words=["a"])


def test_the_http_backend_is_an_aligner():
    t = HttpTranscriber("http://x", model="nb-whisper-small")
    assert isinstance(t, WordAligner)
    assert get_aligner(t) is t


def test_a_transcribe_only_backend_gets_the_null_aligner():
    """One setting chooses the service; a backend that cannot place words
    costs the per-word compare and nothing else."""

    class TranscribeOnly:
        name = "legacy"

        async def transcribe(self, audio, *, content_type, language="no"):
            raise NotImplementedError

    assert get_aligner(TranscribeOnly()).name == "none"


@pytest.mark.anyio
async def test_a_service_without_the_endpoint_is_unavailable_not_a_rejection(speech_service):
    # Found live on 2026-10-17: the running service predated /align and
    # answered 404, which the client filed as "audio rejected" and the words
    # route turned into a 502. An older build is the feature being absent.
    t = speech_service(lambda r: httpx.Response(404, json={"detail": "Not Found"}))
    with pytest.raises(TranscriptionUnavailable):
        await t.align(b"wav", content_type="audio/wav", words=["a"])
