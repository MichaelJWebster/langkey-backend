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
