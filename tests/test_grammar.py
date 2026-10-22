"""Grammar practice: the content file's gates, the menus, rounds of ten, answers.

Michael: *"Could we perhaps have different sections within V2 word order?"*
and *"10 exercises before we ask if the user wants to continue, or go back to
the grammar exercises menu page."*

The content gates run on the authored file, not on a fixture, because an
exercise whose answer cannot be built from its tiles is unwinnable and the
learner would only find out by failing it.
"""

import re

from httpx import AsyncClient
from sqlalchemy import select

import app.db as db
from app.content import GRAMMAR_KINDS, GRAMMAR_TERMS, load_grammar
from app.models import GrammarAttempt, GrammarExercise
from app.routes.grammar import ROUND_SIZE
from app.seed import _sync_grammar
from tests.conftest import login

UNITS = load_grammar("nb")
EXERCISES = [(u, ex) for u in UNITS for ex in u["exercises"]]


def _words(s: str) -> list[str]:
    return re.sub(r"[.,!?…]", " ", s).lower().split()


# --- The content file ---------------------------------------------------------


def test_every_exercise_has_a_unique_slug() -> None:
    slugs = [ex["slug"] for _u, ex in EXERCISES]
    assert len(slugs) == len(set(slugs))


def test_every_exercise_is_a_kind_the_ui_can_draw() -> None:
    for _u, ex in EXERCISES:
        assert ex["kind"] in GRAMMAR_KINDS, ex["slug"]
        assert ex["answers"], f"{ex['slug']}: no accepted answer"
        assert ex.get("why"), f"{ex['slug']}: nothing to say when it goes wrong"


def test_a_choice_always_offers_its_answer() -> None:
    for _u, ex in EXERCISES:
        if ex["kind"] != "choose":
            continue
        assert len(set(ex["options"])) == len(ex["options"]), ex["slug"]
        for answer in ex["answers"]:
            assert answer in ex["options"], f"{ex['slug']}: {answer!r} is not an option"


def test_every_build_answer_uses_exactly_its_tiles() -> None:
    """Tiles are whole phrases ("klokka sju"), so compare words, not tiles."""
    for _u, ex in EXERCISES:
        if ex["kind"] != "build":
            continue
        tile_words = sorted(w for t in ex["tiles"] for w in _words(t))
        for answer in ex["answers"]:
            assert sorted(_words(answer)) == tile_words, f"{ex['slug']}: {answer!r}"


def test_a_fix_starts_from_something_wrong() -> None:
    for _u, ex in EXERCISES:
        if ex["kind"] == "fix":
            assert _words(ex["prompt"]) != _words(ex["answers"][0]), ex["slug"]


def test_a_cue_is_where_the_answer_starts() -> None:
    """"Move the cue to the front" must mean the answer begins with it."""
    for _u, ex in EXERCISES:
        cue = ex.get("cue")
        if not cue or cue.startswith("+"):
            continue
        lead = _words(cue)
        for answer in ex["answers"]:
            assert _words(answer)[: len(lead)] == lead, f"{ex['slug']}: {answer!r}"


def test_a_fill_has_a_gap_and_a_hint() -> None:
    for _u, ex in EXERCISES:
        if ex["kind"] == "fill":
            assert "___" in ex["prompt"], ex["slug"]
            assert ex.get("hint"), ex["slug"]


def test_every_marked_up_term_has_an_explanation() -> None:
    for _u, ex in EXERCISES:
        for key in re.findall(r"\[\[([a-z]+)(?:\|[^\]]+)?\]\]", ex["instr"]):
            assert key in GRAMMAR_TERMS, f"{ex['slug']}: unknown term {key!r}"


def test_v2_is_split_into_rules_with_a_full_round_each() -> None:
    v2 = [u for u in UNITS if u["topic"] == "v2"]
    rules = [u for u in v2 if not u["review"]]
    assert [u["unit"] for u in rules] == ["subj", "adv", "time", "place", "two", "obj", "conj"]
    for u in rules:
        assert len(u["exercises"]) >= ROUND_SIZE, u["unit"]
    review = [u for u in v2 if u["review"]]
    assert len(review) == 1 and review[0]["exercises"] == []


# --- The API ------------------------------------------------------------------


async def test_the_menu_lists_topics_and_their_units(client: AsyncClient) -> None:
    headers = await login(client)
    body = (await client.get("/api/v1/grammar", headers=headers)).json()
    topics = {t["slug"]: t for t in body["topics"]}
    assert list(topics) == ["v2", "q", "neg", "noun", "tense", "pron"]

    v2 = topics["v2"]["units"]
    assert v2[0]["title"] == "Subject first"
    mix = next(u for u in v2 if u["review"])
    # The review draws on every rule in the topic.
    assert mix["total"] == sum(u["total"] for u in v2 if not u["review"])
    assert all(u["seen"] == 0 for u in v2)


async def test_a_round_is_ten_even_from_a_smaller_unit(client: AsyncClient) -> None:
    headers = await login(client)
    body = (await client.get("/api/v1/grammar/neg/main/round", headers=headers)).json()
    slugs = [ex["slug"] for ex in body["exercises"]]
    assert len(slugs) == ROUND_SIZE
    assert len(set(slugs)) == len([u for u in UNITS if u["topic"] == "neg"][0]["exercises"])
    assert all(a != b for a, b in zip(slugs, slugs[1:])), "the same exercise twice running"


async def test_a_miss_comes_back_first(client: AsyncClient) -> None:
    headers = await login(client)
    first = (await client.get("/api/v1/grammar/v2/time/round", headers=headers)).json()
    missed = first["exercises"][4]["slug"]
    for ex in first["exercises"]:
        resp = await client.post(
            "/api/v1/grammar/attempts",
            headers=headers,
            json={"slug": ex["slug"], "correct": ex["slug"] != missed, "answer": "x"},
        )
        assert resp.status_code == 200

    again = (await client.get("/api/v1/grammar/v2/time/round", headers=headers)).json()
    assert again["exercises"][0]["slug"] == missed

    menu = (await client.get("/api/v1/grammar", headers=headers)).json()
    time = next(u for t in menu["topics"] for u in t["units"] if u["slug"] == "time")
    assert (time["seen"], time["right"]) == (10, 9)


async def test_the_latest_answer_replaces_the_miss(client: AsyncClient) -> None:
    headers = await login(client)
    slug = "v2-subj-01"
    for correct in (False, True):
        await client.post(
            "/api/v1/grammar/attempts", headers=headers, json={"slug": slug, "correct": correct}
        )
    menu = (await client.get("/api/v1/grammar", headers=headers)).json()
    subj = next(u for t in menu["topics"] for u in t["units"] if u["slug"] == "subj")
    assert (subj["seen"], subj["right"]) == (1, 1)


async def test_a_review_round_draws_on_several_rules(client: AsyncClient) -> None:
    headers = await login(client)
    body = (await client.get("/api/v1/grammar/v2/mix/round", headers=headers)).json()
    assert len(body["exercises"]) == ROUND_SIZE
    # Each exercise says which rule it came from, so the UI can point at it.
    assert len({ex["unit"] for ex in body["exercises"]}) > 1
    assert "mix" not in {ex["unit"] for ex in body["exercises"]}


async def test_a_round_carries_what_the_ui_needs(client: AsyncClient) -> None:
    headers = await login(client)
    body = (await client.get("/api/v1/grammar/v2/subj/round", headers=headers)).json()
    assert body["title"] == "Subject first"
    for ex in body["exercises"]:
        assert ex["answers"] and ex["why"] and ex["instr"]
        if ex["kind"] == "build":
            assert ex["tiles"]
        if ex["kind"] == "choose":
            assert ex["options"]


async def test_unknown_units_and_exercises_are_404(client: AsyncClient) -> None:
    headers = await login(client)
    assert (await client.get("/api/v1/grammar/v2/nope/round", headers=headers)).status_code == 404
    resp = await client.post(
        "/api/v1/grammar/attempts", headers=headers, json={"slug": "nope", "correct": True}
    )
    assert resp.status_code == 404


async def test_an_edit_keeps_the_exercise_and_its_history(client: AsyncClient) -> None:
    headers = await login(client)
    await client.post(
        "/api/v1/grammar/attempts",
        headers=headers,
        json={"slug": "v2-subj-02", "correct": False},
    )
    async with db.session_factory() as session:
        before = await session.scalar(
            select(GrammarExercise).where(GrammarExercise.slug == "v2-subj-02")
        )

        edited = load_grammar("nb")
        subj = next(u for u in edited if u["unit"] == "subj")
        subj["exercises"][1]["prompt"] = "Han drikker te."
        # And drop one exercise from the file entirely.
        dropped = subj["exercises"].pop()["slug"]
        await _sync_grammar(session, "nb", edited)
        await session.commit()

        after = await session.scalar(
            select(GrammarExercise).where(GrammarExercise.slug == "v2-subj-02")
        )
        assert after.id == before.id
        assert after.data["prompt"] == "Han drikker te."
        attempts = list(
            await session.scalars(
                select(GrammarAttempt).where(GrammarAttempt.exercise_id == after.id)
            )
        )
        assert len(attempts) == 1

    body = (await client.get("/api/v1/grammar/v2/subj/round", headers=headers)).json()
    assert dropped not in {ex["slug"] for ex in body["exercises"]}
