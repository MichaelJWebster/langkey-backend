"""Speaking practice draws on what the learner has met, favouring weak sounds."""

import pytest
from httpx import AsyncClient

from app.audio import PRONUNCIATION_SCALES
from app.routes import recordings
from app.routes.speaking import SESSION_PHRASES
from tests.conftest import login
# Classes and plain helpers import fine across test modules; fixtures do not,
# so the two this file needs are re-declared below.
from tests.test_recordings import _FakeS3


@pytest.fixture
def bucket(monkeypatch):
    fake = _FakeS3()
    monkeypatch.setattr(recordings, "_s3_client", lambda **kw: fake)
    return fake


@pytest.fixture(autouse=True)
def _clear_transcriber_override():
    yield
    from app.main import app as fastapi_app
    from app.speech import get_transcriber

    fastapi_app.dependency_overrides.pop(get_transcriber, None)


async def _practice(client: AsyncClient, headers, **params):
    resp = await client.get("/api/v1/speaking/practice", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _meet_some_cards(client: AsyncClient, headers) -> None:
    """Review a queue so the learner has met something to practise saying."""
    queue = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    for card in queue["cards"]:
        await client.post(
            "/api/v1/reviews", json={"card_id": card["card_id"], "rating": 4}, headers=headers
        )


async def test_nothing_met_yet_offers_nothing_rather_than_erroring(client):
    headers = await login(client)
    body = await _practice(client, headers)
    assert body["phrases"] == []
    assert body["record"] == []


async def test_practice_draws_on_what_has_been_met(client):
    headers = await login(client)
    await _meet_some_cards(client, headers)
    body = await _practice(client, headers, limit=6)

    assert 0 < len(body["phrases"]) <= 6
    for p in body["phrases"]:
        assert p["text"]
        assert p["translation"]
        assert p["audio_url"].startswith("/api/v1/audio/")


async def test_a_session_is_short_enough_to_finish(client):
    """Michael: *"The speaking pronunciation sections are very long. Could we
    just include 6-8 questions in them, instead of the 12 we have now?"*

    Pinned because the number is a judgement about how long he will sit
    recording himself, not an implementation detail: speaking costs more per
    item than any other exercise, so the length that gets finished beats the
    length that covers more.
    """
    headers = await login(client)
    await _meet_some_cards(client, headers)

    body = await _practice(client, headers)

    assert len(body["phrases"]) == SESSION_PHRASES
    assert 6 <= SESSION_PHRASES <= 8, "the range Michael asked for"


async def test_every_phrase_carries_the_whole_speed_ladder(client):
    headers = await login(client)
    await _meet_some_cards(client, headers)
    body = await _practice(client, headers, limit=4)
    for p in body["phrases"]:
        assert [s["scale"] for s in p["speeds"]] == PRONUNCIATION_SCALES
        assert len({s["url"] for s in p["speeds"]}) == len(PRONUNCIATION_SCALES)


async def test_a_phrase_never_repeats_within_one_session(client):
    headers = await login(client)
    await _meet_some_cards(client, headers)
    body = await _practice(client, headers, limit=20)
    keys = [(p["item_id"], p["example_id"]) for p in body["phrases"]]
    assert len(keys) == len(set(keys))


async def test_headwords_and_examples_are_both_offered(client):
    headers = await login(client)
    await _meet_some_cards(client, headers)
    body = await _practice(client, headers, limit=40)
    kinds = {p["example_id"] is None for p in body["phrases"]}
    assert kinds == {True, False}, "expected both headwords and example sentences"


async def test_the_record_is_empty_before_anything_has_been_checked(client):
    headers = await login(client)
    await _meet_some_cards(client, headers)
    body = await _practice(client, headers)
    assert body["record"] == []
    assert body["attempts_for_pattern"] >= 1


async def test_a_struggling_word_is_named_on_the_phrases_that_contain_it(client, bucket):
    """The record steers the session, and says so rather than silently."""
    from tests.test_recordings import (
        _first_example,
        _transcribe,
        _upload_example,
    )
    from app.pronunciation import PATTERN_MIN_ATTEMPTS

    headers = await login(client)
    await _meet_some_cards(client, headers)
    item_id, example = await _first_example(client, headers)
    last = example["sentence"].split()[-1].strip(".,!?").lower()
    spoken = " ".join(example["sentence"].split()[:-1])

    for _ in range(PATTERN_MIN_ATTEMPTS):
        created = (await _upload_example(client, headers, item_id, example["id"])).json()
        await _transcribe(client, headers, created["id"], spoken)

    body = await _practice(client, headers, limit=40)
    worst = body["record"][0]
    assert worst["word"] == last
    assert worst["pattern"] is True

    containing = [p for p in body["phrases"] if last in p["text"].lower()]
    assert containing, "the phrase with the struggling word should be offered"
    assert all(last in p["focus"] for p in containing)


# --- where the words are in a reference clip (ADR-043) ------------------------

from app.audio import audio_object_key  # noqa: E402
from tests.test_recordings import _FakeTranscriber, use_transcriber  # noqa: E402

TEXT = "God kveld! Har dere et ledig bord?"
WORDS = ["god", "kveld", "har", "dere", "et", "ledig", "bord"]


@pytest.fixture
def clips(monkeypatch):
    """The clip store as the speaking route sees it."""
    from app.routes import speaking

    fake = _FakeS3()
    monkeypatch.setattr(speaking, "_s3_client", lambda **kw: fake)
    return fake


def _placed(n: int):
    return [(i * 0.5, i * 0.5 + 0.4) for i in range(n)]


async def test_clip_words_are_placed_once_and_kept(client, clips):
    headers = await login(client)
    key = audio_object_key("nb", TEXT, primary=True, scale=3.4)
    clips.objects[key] = b"RIFF..."
    fake = use_transcriber(_FakeTranscriber(spans=_placed(7)))

    first = await client.get(
        "/api/v1/speaking/words", params={"text": TEXT, "scale": 3.4}, headers=headers
    )

    assert first.status_code == 200, first.text
    body = first.json()
    assert body["url"] == f"/api/v1/{key}"
    assert body["scale"] == 3.4
    assert [w["word"] for w in body["words"]] == WORDS
    assert body["words"][1] == {"word": "kveld", "start": 0.5, "end": 0.9}
    # The clip's bytes and the words, tokenised as the marks are.
    assert fake.align_calls == [(b"RIFF...", "audio/wav", WORDS)]

    again = await client.get(
        "/api/v1/speaking/words", params={"text": TEXT, "scale": 3.4}, headers=headers
    )
    assert again.json() == body
    assert len(fake.align_calls) == 1, "content-addressed: aligned once, for everyone"


async def test_each_speed_is_its_own_clip_with_its_own_timings(client, clips):
    headers = await login(client)
    for scale in (3.4, 2.2):
        clips.objects[audio_object_key("nb", TEXT, primary=True, scale=scale)] = b"wav"
    fake = use_transcriber(_FakeTranscriber(spans=_placed(7)))

    slow = (await client.get("/api/v1/speaking/words", params={"text": TEXT, "scale": 3.4}, headers=headers)).json()
    easier = (await client.get("/api/v1/speaking/words", params={"text": TEXT, "scale": 2.2}, headers=headers)).json()

    assert slow["url"] != easier["url"]
    assert len(fake.align_calls) == 2


async def test_clip_words_need_a_generated_clip(client, clips):
    headers = await login(client)
    use_transcriber(_FakeTranscriber(spans=_placed(7)))
    resp = await client.get("/api/v1/speaking/words", params={"text": TEXT}, headers=headers)
    assert resp.status_code == 404


async def test_clip_words_only_come_from_the_ladder(client, clips):
    headers = await login(client)
    resp = await client.get(
        "/api/v1/speaking/words", params={"text": TEXT, "scale": 1.0}, headers=headers
    )
    assert resp.status_code == 422


async def test_clip_words_report_503_when_the_aligner_is_down(client, clips):
    headers = await login(client)
    clips.objects[audio_object_key("nb", TEXT, primary=True, scale=3.4)] = b"wav"
    use_transcriber(_FakeTranscriber())  # transcribes, cannot align
    resp = await client.get("/api/v1/speaking/words", params={"text": TEXT}, headers=headers)
    assert resp.status_code == 503
