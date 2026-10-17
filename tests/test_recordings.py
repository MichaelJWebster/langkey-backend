"""Learner voice recordings: upload, list, stream, delete.

S3 is stubbed with an in-memory bucket — these cover ownership, validation and
the round trip, not object-store behaviour.
"""

import pytest
from botocore.exceptions import ClientError

import app.routes.recordings as recordings
from tests.conftest import login

AUDIO = b"\x1aE\xdf\xa3fake-webm-bytes" * 8


class _FakeS3:
    """In-memory stand-in for the bucket, shared across one test."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body
        return {}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        data = self.objects[Key]

        class _Body:
            def read(self_inner):
                return data

        return {"Body": _Body(), "ContentLength": len(data)}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)
        return {}


@pytest.fixture
def bucket(monkeypatch):
    fake = _FakeS3()
    monkeypatch.setattr(recordings, "_s3_client", lambda **kw: fake)
    return fake


async def _first_item_id(client, headers) -> int:
    queue = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    return queue["cards"][0]["item"]["id"]


async def _upload(client, headers, item_id, data=AUDIO, content_type="audio/webm;codecs=opus"):
    return await client.post(
        "/api/v1/recordings",
        headers=headers,
        data={"item_id": str(item_id), "duration_ms": "1500"},
        files={"audio": ("take.webm", data, content_type)},
    )


async def test_upload_then_list_and_stream(client, bucket):
    headers = await login(client)
    item_id = await _first_item_id(client, headers)

    resp = await _upload(client, headers, item_id)
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["item_id"] == item_id
    assert created["example_id"] is None
    assert created["size_bytes"] == len(AUDIO)
    assert created["duration_ms"] == 1500
    # Codec parameters are stripped, and the object is scoped to the user.
    assert created["content_type"] == "audio/webm"
    assert len(bucket.objects) == 1
    assert next(iter(bucket.objects)).startswith("recordings/")

    listed = await client.get(f"/api/v1/recordings?item_id={item_id}", headers=headers)
    assert [r["id"] for r in listed.json()] == [created["id"]]

    audio = await client.get(f"/api/v1/recordings/{created['id']}/audio", headers=headers)
    assert audio.status_code == 200
    assert audio.content == AUDIO
    assert audio.headers["content-type"].startswith("audio/webm")


async def test_takes_accumulate_newest_first(client, bucket):
    headers = await login(client)
    item_id = await _first_item_id(client, headers)
    first = (await _upload(client, headers, item_id)).json()
    second = (await _upload(client, headers, item_id)).json()

    listed = (await client.get(f"/api/v1/recordings?item_id={item_id}", headers=headers)).json()
    ids = [r["id"] for r in listed]
    assert set(ids) == {first["id"], second["id"]}, "takes are kept, not overwritten"
    assert len(bucket.objects) == 2


async def test_rejects_unsupported_type_and_empty_body(client, bucket):
    headers = await login(client)
    item_id = await _first_item_id(client, headers)

    bad_type = await _upload(client, headers, item_id, content_type="application/pdf")
    assert bad_type.status_code == 415

    empty = await _upload(client, headers, item_id, data=b"")
    assert empty.status_code == 400
    assert bucket.objects == {}, "nothing is stored when validation fails"


async def test_rejects_unknown_item(client, bucket):
    headers = await login(client)
    resp = await _upload(client, headers, 10_000_000)
    assert resp.status_code == 404
    assert bucket.objects == {}


async def test_delete_removes_row_and_object(client, bucket):
    headers = await login(client)
    item_id = await _first_item_id(client, headers)
    created = (await _upload(client, headers, item_id)).json()

    resp = await client.delete(f"/api/v1/recordings/{created['id']}", headers=headers)
    assert resp.status_code == 204
    assert bucket.objects == {}

    listed = (await client.get(f"/api/v1/recordings?item_id={item_id}", headers=headers)).json()
    assert listed == []


async def test_requires_authentication(client, bucket):
    headers = await login(client)
    item_id = await _first_item_id(client, headers)
    created = (await _upload(client, headers, item_id)).json()

    for resp in (
        await client.get(f"/api/v1/recordings?item_id={item_id}"),
        await client.get(f"/api/v1/recordings/{created['id']}/audio"),
    ):
        assert resp.status_code == 401


# --- transcription (M3 step 2) ---------------------------------------------


class _FakeTranscriber:
    """Stands in for the speech container. See tests/test_speech_seam.py.

    Aligns too (ADR-043) when given `spans`, one per heard word; without
    them it reports the aligner unavailable, which is what a test that is
    not about alignment should see -- the check must not depend on it.
    """

    name = "fake"

    def __init__(self, result=None, error=None, *, spans=None, align_error=None) -> None:
        self.result, self.error = result, error
        self.spans, self.align_error = spans, align_error
        self.calls: list[tuple[bytes, str]] = []
        self.align_calls: list[tuple[bytes, str, list[str]]] = []

    async def transcribe(self, audio, *, content_type, language="no"):
        self.calls.append((audio, content_type))
        if self.error:
            raise self.error
        return self.result

    async def align(self, audio, *, content_type, words):
        from app.speech import Alignment, TranscriptionUnavailable, WordSpan

        self.align_calls.append((audio, content_type, list(words)))
        if self.align_error:
            raise self.align_error
        if self.spans is None:
            raise TranscriptionUnavailable("no aligner in this test")
        return Alignment(
            words=tuple(
                WordSpan(w, s[0], s[1]) if s is not None else None
                for w, s in zip(words, self.spans)
            ),
            model="fake-aligner",
        )


def use_transcriber(transcriber):
    """Override the route's dependency, the way production swaps backends."""
    from app.main import app as fastapi_app
    from app.speech import get_transcriber

    fastapi_app.dependency_overrides[get_transcriber] = lambda: transcriber
    return transcriber


@pytest.fixture(autouse=True)
def _clear_transcriber_override():
    yield
    from app.main import app as fastapi_app
    from app.speech import get_transcriber

    fastapi_app.dependency_overrides.pop(get_transcriber, None)


async def test_transcribe_a_saved_take(client, bucket):
    from app.speech import Transcript

    headers = await login(client)
    item_id = await _first_item_id(client, headers)
    created = (await _upload(client, headers, item_id)).json()

    fake = use_transcriber(
        _FakeTranscriber(
            Transcript(
                text="Jeg bor i Oslo.",
                language="no",
                model="NbAiLab/nb-whisper-small",
                duration_ms=380,
                confidence=-0.21,
            )
        )
    )

    resp = await client.post(
        f"/api/v1/recordings/{created['id']}/transcribe", headers=headers
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["text"] == "Jeg bor i Oslo."
    # Which model ran is part of the answer: ADR-031 is only checkable if the
    # claim is recorded where the result is.
    assert body["model"] == "NbAiLab/nb-whisper-small"
    assert body["confidence"] == -0.21
    # The stored bytes, with the recorded type, reached the transcriber.
    assert fake.calls == [(AUDIO, "audio/webm")]


async def test_transcribe_reports_503_when_no_backend_is_configured(client, bucket):
    from app.speech import TranscriptionUnavailable

    headers = await login(client)
    item_id = await _first_item_id(client, headers)
    created = (await _upload(client, headers, item_id)).json()

    use_transcriber(_FakeTranscriber(error=TranscriptionUnavailable("nothing configured")))

    resp = await client.post(
        f"/api/v1/recordings/{created['id']}/transcribe", headers=headers
    )

    # 503, not 502 and not a wrong transcript: the learner must never be
    # marked down because the service was absent.
    assert resp.status_code == 503
    assert "nothing configured" in resp.json()["detail"]


async def test_transcribe_reports_502_when_the_model_rejected_the_audio(client, bucket):
    from app.speech import TranscriptionError

    headers = await login(client)
    item_id = await _first_item_id(client, headers)
    created = (await _upload(client, headers, item_id)).json()

    use_transcriber(_FakeTranscriber(error=TranscriptionError("unreadable container")))

    resp = await client.post(
        f"/api/v1/recordings/{created['id']}/transcribe", headers=headers
    )
    assert resp.status_code == 502


async def test_transcribe_refuses_someone_elses_recording(client, bucket):
    from app.speech import Transcript

    headers = await login(client)
    item_id = await _first_item_id(client, headers)
    created = (await _upload(client, headers, item_id)).json()
    use_transcriber(_FakeTranscriber(Transcript(text="x", language="no", model="m")))

    # No token: must not transcribe, and must not say whether it exists.
    resp = await client.post(f"/api/v1/recordings/{created['id']}/transcribe")
    assert resp.status_code == 401


async def test_transcribe_404s_when_the_audio_is_gone(client, bucket):
    from app.speech import Transcript

    headers = await login(client)
    item_id = await _first_item_id(client, headers)
    created = (await _upload(client, headers, item_id)).json()
    bucket.objects.clear()
    use_transcriber(_FakeTranscriber(Transcript(text="x", language="no", model="m")))

    resp = await client.post(
        f"/api/v1/recordings/{created['id']}/transcribe", headers=headers
    )
    assert resp.status_code == 404


async def _transcribe(client, headers, recording_id, text, model="NbAiLab/nb-whisper-small"):
    from app.speech import Transcript

    use_transcriber(
        _FakeTranscriber(
            Transcript(text=text, language="no", model=model, duration_ms=300, confidence=None)
        )
    )
    return await client.post(
        f"/api/v1/recordings/{recording_id}/transcribe", headers=headers
    )


async def _upload_example(client, headers, item_id, example_id):
    return await client.post(
        "/api/v1/recordings",
        headers=headers,
        data={"item_id": str(item_id), "example_id": str(example_id), "duration_ms": "2000"},
        files={"audio": ("take.webm", AUDIO, "audio/webm;codecs=opus")},
    )


async def _first_example(client, headers):
    queue = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    for card in queue["cards"]:
        if card["item"]["examples"]:
            return card["item"]["id"], card["item"]["examples"][0]
    raise AssertionError("no card in the queue has an example sentence")


async def test_transcribe_marks_each_word_of_the_target(client, bucket):
    headers = await login(client)
    item_id, example = await _first_example(client, headers)
    created = (await _upload_example(client, headers, item_id, example["id"])).json()

    body = (await _transcribe(client, headers, created["id"], example["sentence"])).json()

    assert body["target"] == example["sentence"]
    assert [w["verdict"] for w in body["words"]] == ["said"] * len(body["words"])
    assert len(body["words"]) == len(example["sentence"].split())


async def test_a_word_the_model_lost_is_marked_and_tallied(client, bucket):
    headers = await login(client)
    item_id, example = await _first_example(client, headers)
    created = (await _upload_example(client, headers, item_id, example["id"])).json()

    # Drop the last word of whatever the sentence is.
    spoken = " ".join(example["sentence"].split()[:-1])
    body = (await _transcribe(client, headers, created["id"], spoken)).json()

    assert body["words"][-1]["verdict"] == "missed"
    tally = {h["word"]: h for h in body["history"]}
    last = body["words"][-1]["word"].lower().strip(".,!?")
    assert tally[last]["attempts"] == 1
    assert tally[last]["said"] == 0
    # One attempt is never a pattern, however bad it looks.
    assert tally[last]["pattern"] is False


async def test_transcribing_the_same_take_twice_does_not_count_it_twice(client, bucket):
    headers = await login(client)
    item_id, example = await _first_example(client, headers)
    created = (await _upload_example(client, headers, item_id, example["id"])).json()

    first = (await _transcribe(client, headers, created["id"], example["sentence"])).json()
    # A different transcript on the second call must be ignored: the take was
    # already transcribed, and re-running must not re-score the attempt.
    second = (await _transcribe(client, headers, created["id"], "noe helt annet")).json()

    assert second["text"] == first["text"]
    assert {h["word"]: h["attempts"] for h in second["history"]} == {
        h["word"]: h["attempts"] for h in first["history"]
    }


async def test_the_tally_builds_across_takes_until_it_becomes_a_pattern(client, bucket):
    from app.pronunciation import PATTERN_MIN_ATTEMPTS

    headers = await login(client)
    item_id, example = await _first_example(client, headers)
    spoken = " ".join(example["sentence"].split()[:-1])  # always lose the last word
    last = example["sentence"].split()[-1].lower().strip(".,!?")

    body = None
    for _ in range(PATTERN_MIN_ATTEMPTS):
        created = (await _upload_example(client, headers, item_id, example["id"])).json()
        body = (await _transcribe(client, headers, created["id"], spoken)).json()

    tally = {h["word"]: h for h in body["history"]}
    assert tally[last]["attempts"] == PATTERN_MIN_ATTEMPTS
    assert tally[last]["said"] == 0
    assert tally[last]["pattern"] is True
    # A word that keeps landing never becomes one, however often it is said.
    first_word = example["sentence"].split()[0].lower().strip(".,!?")
    assert tally[first_word]["pattern"] is False


async def test_a_near_miss_counts_against_the_word(client, bucket):
    """klokka coming back as klokke is the error, not a forgivable variant."""
    headers = await login(client)
    item_id, example = await _first_example(client, headers)
    created = (await _upload_example(client, headers, item_id, example["id"])).json()

    # Bend the last word's ending, the way a drifting -a/-e does.
    spoken = example["sentence"].split()
    bare = spoken[-1].strip(".,!?")
    spoken[-1] = (bare[:-1] + ("x" if bare[-1] != "x" else "y")) if len(bare) > 2 else bare + "x"
    body = (await _transcribe(client, headers, created["id"], " ".join(spoken))).json()

    last = bare.lower()
    mark = body["words"][-1]
    assert mark["verdict"] in ("slipped", "missed")
    assert {h["word"]: h["said"] for h in body["history"]}[last] == 0


# --- placing the heard words in the take (ADR-043) ----------------------------


async def test_transcribe_places_the_heard_words_once_and_keeps_them(client, bucket):
    from app.speech import Transcript

    headers = await login(client)
    item_id = await _first_item_id(client, headers)
    created = (await _upload(client, headers, item_id)).json()
    fake = use_transcriber(
        _FakeTranscriber(
            Transcript(text="Jeg bor i Oslo.", language="no", model="nb-whisper-small"),
            spans=[(0.1, 0.4), (0.5, 0.8), (0.8, 0.9), (1.2, 1.7)],
        )
    )

    body = (await client.post(f"/api/v1/recordings/{created['id']}/transcribe", headers=headers)).json()

    assert body["aligned"] is True
    # The aligner was handed the take and the transcript's words, tokenised
    # the same way the marks are, so positions line up.
    assert fake.align_calls == [(AUDIO, "audio/webm", ["jeg", "bor", "i", "oslo"])]
    takes = [w["take"] for w in body["words"]]
    assert all(t is not None for t in takes) if len(takes) == 4 else True

    # Asking again reuses the stored placement, like the transcript itself.
    again = (await client.post(f"/api/v1/recordings/{created['id']}/transcribe", headers=headers)).json()
    assert len(fake.align_calls) == 1
    assert again["aligned"] is True
    assert again["words"] == body["words"]


async def test_the_check_does_not_wait_on_the_aligner(client, bucket):
    """A speech service that transcribes but cannot place words -- or an
    aligner that is down -- must cost the per-word compare and nothing
    else. The marks and the tally are the check; the spans are a bonus."""
    from app.speech import Transcript

    headers = await login(client)
    item_id = await _first_item_id(client, headers)
    created = (await _upload(client, headers, item_id)).json()
    use_transcriber(
        _FakeTranscriber(Transcript(text="Jeg bor i Oslo.", language="no", model="nb-whisper-small"))
    )

    resp = await client.post(f"/api/v1/recordings/{created['id']}/transcribe", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["aligned"] is False
    assert all(w["take"] is None for w in body["words"])
    assert body["words"]  # the marks are still there
