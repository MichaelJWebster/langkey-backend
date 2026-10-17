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



# --- word alignment (ADR-043) ---------------------------------------------------

from speech_service import aligner  # noqa: E402 - grouped with the tests it serves


@pytest.mark.anyio
async def test_health_reports_the_aligner_without_loading_it(service):
    body = (await service.get("/health")).json()
    # ADR-031 holds for the aligner too: Norwegian-trained, by NbAiLab.
    assert "nb-wav2vec2" in body["aligner"]
    assert body["aligner_loaded"] is False


@pytest.mark.anyio
async def test_align_returns_one_span_per_word_sent(service, monkeypatch):
    sent = {}

    def fake(audio, words, settings):
        sent["words"] = words
        return {
            "words": [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate(words)],
            "model": settings.aligner,
            "duration_ms": 12,
        }

    monkeypatch.setattr(aligner, "align", fake)
    resp = await service.post(
        "/align",
        files={"audio": ("clip.wav", b"bytes", "audio/wav")},
        data={"words": '["god", "kveld"]'},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert sent["words"] == ["god", "kveld"]
    assert [w["word"] for w in body["words"]] == ["god", "kveld"]
    assert body["words"][1] == {"word": "kveld", "start": 0.5, "end": 0.9}


@pytest.mark.anyio
@pytest.mark.parametrize("words", ["not json", '"a string"', "[]", "[1, 2]"])
async def test_align_rejects_words_that_are_not_a_list_of_strings(service, monkeypatch, words):
    monkeypatch.setattr(aligner, "align", lambda *a: pytest.fail("the model must not run"))
    resp = await service.post(
        "/align", files={"audio": ("clip.wav", b"bytes", "audio/wav")}, data={"words": words}
    )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_an_alignment_failure_becomes_a_500(service, monkeypatch):
    def fail(audio, words, settings):
        raise RuntimeError("no audio stream")

    monkeypatch.setattr(aligner, "align", fail)
    resp = await service.post(
        "/align", files={"audio": ("clip.wav", b"bytes", "audio/wav")}, data={"words": '["a"]'}
    )
    assert resp.status_code == 500
    assert "no audio stream" in resp.json()["detail"]


# The trellis and the boundary rule, without a model: synthetic emissions
# where the right answer is known.

np = pytest.importorskip("numpy")

BLANK = 0


def emissions(letters_per_frame: list[int], vocab: int = 4) -> "np.ndarray":
    """Log-probs that strongly prefer the given token on each frame."""
    logp = np.full((len(letters_per_frame), vocab), np.log(0.01))
    for t, tok in enumerate(letters_per_frame):
        logp[t, tok] = np.log(0.97)
    return logp


def test_viterbi_follows_an_unambiguous_path():
    # a a _ b _ _ : "ab" spelt out with blanks between.
    path = aligner.viterbi(emissions([1, 1, BLANK, 2, BLANK, BLANK]), [1, 2], BLANK)
    assert path == [0, 0, -1, 1, -1, -1]


def test_viterbi_needs_a_blank_between_repeated_letters():
    # "aa": the second a cannot follow the first without a blank, so the
    # path has to spend one of the a-frames on it.
    path = aligner.viterbi(emissions([1, 1, 1, 1]), [1, 1], BLANK)
    assert path[0] == 0 and path[-1] == 1
    assert -1 in path


def test_viterbi_places_every_letter_even_against_the_emissions():
    # The audio "says" only a; the text says ab. Forced means forced: b
    # still gets a frame, at the end where it costs least.
    path = aligner.viterbi(emissions([1, 1, 1, 1]), [1, 2], BLANK)
    assert 1 in path


def test_letter_spans_cover_first_to_last_frame_of_each_word():
    path = [-1, 0, 0, -1, 1, -1, 2, 2, 2, -1]
    spans = aligner.letter_spans(path, [(0, 1), (2, 2)])
    assert spans == [(1, 4), (6, 8)]


def test_letter_spans_skip_a_word_with_no_letters():
    spans = aligner.letter_spans([0, 0, -1, 1], [(0, 0), None, (1, 1)])
    assert spans == [(0, 1), None, (3, 3)]


def test_refine_grows_a_word_out_to_the_silence_either_side():
    # Frames: 0-1 silent, 2-7 voiced, 8-9 silent, 10-15 voiced, 16-19 silent.
    rms = np.array([0, 0, 1, 1, 1, 1, 1, 1, 0, 0, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0], float)
    # The letters only lit frames 4-5 and 12-13: CTC spikes.
    spans = aligner.refine([(4, 5), (12, 13)], rms, floor=0.5)
    assert spans == [(2, 8), (10, 16)]


def test_refine_splits_touching_words_between_their_letters():
    # No silence anywhere: two words run together.
    rms = np.ones(12)
    spans = aligner.refine([(1, 3), (8, 10)], rms, floor=0.5)
    # The boundary sits at the midpoint of the letter gap (frames 4..8 -> 6).
    assert spans[0][1] == spans[1][0] == 6
    assert spans[0][0] == 0 and spans[1][1] == 12


def test_refine_does_not_cross_a_neighbours_letters():
    rms = np.ones(10)
    spans = aligner.refine([(0, 2), (3, 4), (6, 9)], rms, floor=0.5)
    starts = [s for s, _ in spans]
    ends = [e for _, e in spans]
    assert starts == sorted(starts) and ends == sorted(ends)
    for (_, e), (s, _) in zip(spans, spans[1:]):
        assert e == s


def test_refine_is_bounded_in_the_lead_in_and_tail():
    # A long voiced run-up (room noise above the floor) must not all become
    # the first word.
    rms = np.ones(200)
    spans = aligner.refine([(100, 101)], rms, floor=0.5)
    assert spans[0][0] >= 100 - aligner.MAX_GROW_FRAMES
    assert spans[0][1] <= 102 + aligner.MAX_GROW_FRAMES


def test_refine_leaves_an_unplaced_word_unplaced():
    rms = np.ones(10)
    spans = aligner.refine([(0, 1), None, (6, 8)], rms, floor=0.5)
    assert spans[1] is None
    assert spans[0] is not None and spans[2] is not None


def test_tokens_drop_what_the_model_cannot_spell_rather_than_failing():
    vocab = {"|": 0, "a": 1, "b": 2, "å": 3}
    ids, groups = aligner._tokens(["ab", "42", "å"], vocab, delimiter=0)
    assert ids == [1, 2, 0, 3]
    assert groups == [(0, 1), None, (3, 3)]
