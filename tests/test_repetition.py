"""The repetition deck: keep what is met, and favour what is not landing."""

from httpx import AsyncClient

from app.routes.reviews import _repeat_weight
from app.sampling import weighted_sample
from tests.conftest import login


class _Card:
    """Just the fields _repeat_weight reads."""

    def __init__(self, *, fsrs_state=2, lapses=0, difficulty=None):
        self.fsrs_state = fsrs_state
        self.lapses = lapses
        self.difficulty = difficulty


def test_weighted_sample_never_repeats_an_index() -> None:
    picked = weighted_sample([100.0, 1.0, 1.0], 3)
    assert sorted(picked) == [0, 1, 2]


def test_weighted_sample_caps_at_the_pool_size() -> None:
    assert len(weighted_sample([1.0, 1.0], 10)) == 2


def test_weighted_sample_favours_heavy_entries() -> None:
    # Not a distribution test, just that the weight is doing something: over
    # many single draws the heavy index must dominate.
    firsts = [weighted_sample([20.0, 1.0], 1)[0] for _ in range(200)]
    assert firsts.count(0) > firsts.count(1)


def test_weighted_sample_handles_all_zero_weights() -> None:
    assert sorted(weighted_sample([0.0, 0.0, 0.0], 2)) == [0, 1]


def test_due_and_struggling_cards_outweigh_solid_ones() -> None:
    solid = _Card(difficulty=2.0)
    lapsing = _Card(lapses=5, difficulty=9.0)
    unestablished = _Card(fsrs_state=1)
    assert _repeat_weight(solid, due_now=True) > _repeat_weight(solid, due_now=False)
    assert _repeat_weight(lapsing, due_now=False) > _repeat_weight(solid, due_now=False)
    assert _repeat_weight(unestablished, due_now=False) > _repeat_weight(solid, due_now=False)


def test_lapse_weight_is_capped() -> None:
    assert _repeat_weight(_Card(lapses=50), due_now=False) == _repeat_weight(
        _Card(lapses=4), due_now=False
    )


async def test_repeat_deck_is_empty_before_anything_is_met(client: AsyncClient) -> None:
    headers = await login(client)
    body = (await client.get("/api/v1/reviews/queue?mode=repeat", headers=headers)).json()
    assert body["cards"] == []
    assert body["new_count"] == 0


async def _meet_the_first_batch(client: AsyncClient, headers: dict) -> set[str]:
    first = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    for card in first["cards"]:
        await client.post(
            "/api/v1/reviews", json={"card_id": card["card_id"], "rating": 3}, headers=headers
        )
    return {c["card_id"] for c in first["cards"]}


async def test_repeat_deck_draws_only_met_words_and_adds_none(client: AsyncClient) -> None:
    headers = await login(client)
    met = await _meet_the_first_batch(client, headers)

    body = (
        await client.get("/api/v1/reviews/queue?mode=repeat&limit=40", headers=headers)
    ).json()
    assert body["cards"], "expected the met words back"
    # Nothing new: the repetition deck never grows the pile.
    assert body["new_count"] == 0
    assert all(not c["is_new"] for c in body["cards"])
    assert {c["card_id"] for c in body["cards"]} <= met


async def test_repeat_deck_can_be_scoped_to_one_category(client: AsyncClient) -> None:
    headers = await login(client)
    await _meet_the_first_batch(client, headers)
    body = (
        await client.get(
            "/api/v1/reviews/queue?mode=repeat&limit=40&category=food", headers=headers
        )
    ).json()
    assert all("food" in c["item"]["categories"] for c in body["cards"])
