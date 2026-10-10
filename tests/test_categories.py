"""Vocabulary categories: the mapping, the counts, and overlap."""

from httpx import AsyncClient

from app.categories import CATEGORIES, TOPIC_MAP, categories_for_topic, label
from app.content import merge_local_deck
from tests.conftest import login


def test_every_mapped_category_is_a_real_category() -> None:
    for topic, cats in TOPIC_MAP.items():
        for c in cats:
            assert c in CATEGORIES, f"{topic} -> {c}"


def test_unknown_and_missing_topics_fall_back_to_essentials() -> None:
    assert categories_for_topic(None) == ("essentials",)
    assert categories_for_topic("astrophysics") == ("essentials",)


def test_label_is_readable_for_an_unknown_slug() -> None:
    assert label("essentials") == "Everyday essentials"
    assert label("deep-sea-diving") == "Deep sea diving"


def test_every_deck_entry_has_at_least_one_known_category() -> None:
    for e in merge_local_deck("nb"):
        assert e["categories"], e["no"]
        for c in e["categories"]:
            assert c in CATEGORIES, (e["no"], c)


def test_deck_has_an_a1_vocabulary_of_the_agreed_size() -> None:
    """500-1000 words at A1, which is what Michael asked for.

    The deck also carries A2 entries from before this was the target. They
    stay: he has progress on some of them, and the upsert key includes the
    wording, so dropping one would orphan its card.
    """
    entries = merge_local_deck("nb")
    assert 500 <= len(entries) <= 1000, len(entries)
    a1 = [e for e in entries if e["cefr"] == "A1"]
    assert len(a1) >= 500, len(a1)


def test_no_category_is_left_empty() -> None:
    used = {c for e in merge_local_deck("nb") for c in e["categories"]}
    assert used == set(CATEGORIES), set(CATEGORIES) - used


async def test_a_word_in_two_categories_counts_as_learned_in_both(
    client: AsyncClient,
) -> None:
    headers = await login(client)
    queue = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    shared = next(
        (c for c in queue["cards"] if len(c["item"]["categories"]) > 1),
        None,
    )
    if shared is None:  # the front of the deck is all single-category
        return
    # Four Easy ratings graduate the card to FSRS Review state.
    for _ in range(4):
        await client.post(
            "/api/v1/reviews", json={"card_id": shared["card_id"], "rating": 4}, headers=headers
        )

    body = (await client.get("/api/v1/categories", headers=headers)).json()
    by_slug = {c["category"]: c for c in body["categories"]}
    for slug in shared["item"]["categories"]:
        assert by_slug[slug]["established"] >= 1, slug
    # ... and the whole-deck figure counts that one word once.
    assert body["overall"]["established"] == 1
