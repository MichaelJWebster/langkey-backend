"""The clip a learner copies must not be the clip they listen to (ADR-033).

Reviews serve two URLs per sentence: audio_url varies the speaker so the
learner hears more than one voice, and copy_audio_url is always the clearest
voice at a rate a learner can reproduce. Keeping them apart is the whole
point, so these tests fail if they ever collapse back into one.
"""

import pytest
from httpx import AsyncClient

from app.audio import (
    DICTATION_SCALES,
    LENGTH_SCALE,
    PRONUNCIATION_SCALE,
    PRIMARY_VOICE,
    VOICE_POOL,
    audio_object_key,
    audio_url,
    voice_for_text,
)
from tests.conftest import login

# Sentences whose hash lands on a pool speaker rather than the primary voice,
# which is the case where the two URLs have to differ.
POOL_SENTENCES = [
    t
    for t in (
        "Dette er en gammel kirke.",
        "Vær så snill, snakk litt saktere.",
        "Vent litt, vær så snill.",
        "Kan du hjelpe meg, vær så snill?",
        "Jeg spiser en banan før trening.",
        "Bananen er gul.",
    )
    if voice_for_text(t) != PRIMARY_VOICE
]


def test_there_are_pool_sentences_to_test_with():
    assert POOL_SENTENCES, "hash fixture drifted; pick sentences that land on nvcc"


def test_imitation_is_slower_than_every_listening_clip():
    assert PRONUNCIATION_SCALE > LENGTH_SCALE


def test_imitation_is_slower_than_the_whole_dictation_ladder():
    # Dictation is comprehension practice and wants a pace you have to keep up
    # with; imitation wants one you can reproduce. The two ladders separated
    # when the learner asked for "at least 50%" slower, so this is the line
    # that stops a later retune quietly merging them again.
    assert PRONUNCIATION_SCALE > max(DICTATION_SCALES)


@pytest.mark.parametrize("sentence", POOL_SENTENCES)
def test_copy_clip_differs_from_the_listening_clip(sentence):
    listening = audio_url("nb", sentence)
    copying = audio_url("nb", sentence, primary=True, scale=PRONUNCIATION_SCALE)
    assert listening != copying


@pytest.mark.parametrize("sentence", POOL_SENTENCES)
def test_copy_clip_never_comes_from_the_pool(sentence):
    # The nvcc speakers mumble their first word (ADR-025), so they must never
    # be what a learner is asked to reproduce.
    assert voice_for_text(sentence, primary=True) == PRIMARY_VOICE
    assert any(v.startswith("nvcc:") for v in VOICE_POOL)


def test_scale_is_part_of_the_key_so_speeds_cannot_collide():
    a = audio_object_key("nb", "Bananen er gul.", primary=True, scale=LENGTH_SCALE)
    b = audio_object_key("nb", "Bananen er gul.", primary=True, scale=PRONUNCIATION_SCALE)
    assert a != b


async def test_review_queue_serves_both_urls(client: AsyncClient) -> None:
    headers = await login(client)
    body = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    cards = body["cards"]
    assert cards
    for card in cards:
        item = card["item"]
        assert item["copy_audio_url"].startswith("/api/v1/audio/")
        assert item["copy_audio_url"] != "", item
        for ex in item["examples"]:
            assert ex["copy_audio_url"].startswith("/api/v1/audio/")
            # The listening clip may legitimately equal the copy clip when the
            # hash happens to pick the primary voice -- but only then.
            if voice_for_text(ex["sentence"]) != PRIMARY_VOICE:
                assert ex["copy_audio_url"] != ex["audio_url"]
