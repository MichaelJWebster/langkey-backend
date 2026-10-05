"""The speech service's HTTP shell, without loading a model.

transcriber.transcribe is stubbed: what matters here is that the service
validates the request, reports its configured model without downloading it,
and turns any model failure into a 5xx -- because the API maps 5xx to
"unavailable", which is what keeps a broken container from being scored as
bad pronunciation.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from speech_service import transcriber
from speech_service.main import app


@pytest.fixture
async def service():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://speech"
    ) as client:
        yield client


@pytest.mark.anyio
async def test_health_reports_the_model_without_loading_it(service):
    body = (await service.get("/health")).json()

    assert body["status"] == "ok"
    # ADR-031: the default must be a Norwegian-trained model, not plain Whisper.
    assert "nb-whisper" in body["model"]
    assert body["loaded"] is False


@pytest.mark.anyio
async def test_transcribe_returns_what_the_model_heard(service, monkeypatch):
    monkeypatch.setattr(
        transcriber,
        "transcribe",
        lambda audio, settings, language="no": {
            "text": "Jeg bor i Oslo.",
            "language": language,
            "model": settings.model,
            "duration_ms": 300,
            "confidence": None,
            "segments": ["Jeg bor i Oslo."],
        },
    )

    resp = await service.post(
        "/transcribe",
        files={"audio": ("take.webm", b"bytes", "audio/webm")},
        data={"language": "no"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["text"] == "Jeg bor i Oslo."


@pytest.mark.anyio
async def test_empty_audio_is_rejected_as_a_bad_request(service):
    resp = await service.post(
        "/transcribe", files={"audio": ("take.webm", b"", "audio/webm")}
    )
    # 400, so the API reports a failed attempt rather than unavailability.
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_oversized_audio_is_rejected_before_the_model_runs(service, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("the model must not be reached for oversized audio")

    monkeypatch.setattr(transcriber, "transcribe", explode)

    resp = await service.post(
        "/transcribe",
        files={"audio": ("take.webm", b"x" * (26 * 1024 * 1024), "audio/webm")},
    )
    assert resp.status_code == 413


@pytest.mark.anyio
async def test_a_model_failure_becomes_a_500(service, monkeypatch):
    # A corrupt container or a failed load. The API turns this into
    # TranscriptionUnavailable, never into a wrong transcript.
    def fail(audio, settings, language="no"):
        raise RuntimeError("could not decode")

    monkeypatch.setattr(transcriber, "transcribe", fail)

    resp = await service.post(
        "/transcribe", files={"audio": ("take.webm", b"bytes", "audio/webm")}
    )
    assert resp.status_code == 500
    assert "could not decode" in resp.json()["detail"]


def test_device_resolution_never_guesses_cuda_on_this_machine():
    # "auto" must not pick a device that is not there; an explicit choice is
    # always honoured so a container can force cpu.
    assert transcriber.resolve_device("cpu") == "cpu"
    assert transcriber.resolve_device("mps") == "mps"

