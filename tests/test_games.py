"""Games: the authored sets, and the one piece of progress a game keeps.

Michael: *"I'd like a new section after speaking on the main page. Call it
games... Initially just create a flashcards subsection with one set of flash
cards. The set should be called 'question words'."*
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

import app.db as db
from app.content import load_games
from app.models import AppMeta, GamePair, GameRound, GameSet
from app.seed import _sync_games
from tests.conftest import login


async def test_the_question_words_set_is_served(client: AsyncClient) -> None:
    headers = await login(client)
    body = (await client.get("/api/v1/games", headers=headers)).json()

    assert len(body["sets"]) == len(load_games("nb"))
    qw = next(s for s in body["sets"] if s["slug"] == "question-words")
    assert qw["title"] == "Question words"
    assert qw["game"] == "matching"

    pairs = {p["en"]: p["no"] for p in qw["pairs"]}
    # The six Michael asked for.
    assert pairs["what"] == "hva"
    assert pairs["who"] == "hvem"
    assert pairs["where"] == "hvor"
    assert pairs["when"] == "når"
    assert pairs["why"] == "hvorfor"
    assert pairs["how"] == "hvordan"
    # And the hvor- compounds, which are the pattern worth drilling.
    assert pairs["how much"] == "hvor mye"
    assert pairs["how many"] == "hvor mange"
    assert pairs["how long"] == "hvor lenge"
    assert pairs["how far"] == "hvor langt"


async def test_both_sides_of_every_pair_are_unique(client: AsyncClient) -> None:
    """A matching game needs one right answer per card.

    Two cards reading "how long" would be unplayable rather than hard, which
    is what the `note` field exists to prevent.

    The two sides are checked differently on purpose: the note disambiguates
    the English, but the board never shows it on the Norwegian, because
    "hvor lenge (time)" would hand over the match it is meant to test. So the
    Norwegian has to be unique on its own.
    """
    headers = await login(client)
    for s in (await client.get("/api/v1/games", headers=headers)).json()["sets"]:
        labels = [f"{p['en']} ({p['note']})" if p["note"] else p["en"] for p in s["pairs"]]
        assert len(set(labels)) == len(labels), f"{s['slug']}: duplicate English"
        assert len({p["no"] for p in s["pairs"]}) == len(s["pairs"]), (
            f"{s['slug']}: duplicate Norwegian"
        )


async def test_the_ambiguous_pairs_carry_a_note(client: AsyncClient) -> None:
    headers = await login(client)
    qw = next(
        s
        for s in (await client.get("/api/v1/games", headers=headers)).json()["sets"]
        if s["slug"] == "question-words"
    )
    notes = {p["en"]: p["note"] for p in qw["pairs"]}
    assert notes["how long"] == "time"
    assert notes["how far"] == "distance"
    assert notes["what"] is None, "a note on an unambiguous word is just noise"


async def test_nothing_is_recorded_until_a_round_is_finished(
    client: AsyncClient,
) -> None:
    headers = await login(client)
    qw = next(
        s
        for s in (await client.get("/api/v1/games", headers=headers)).json()["sets"]
        if s["slug"] == "question-words"
    )
    assert qw["played"] == 0
    assert qw["best"] is None


async def test_a_finished_round_becomes_the_best(client: AsyncClient) -> None:
    headers = await login(client)
    resp = await client.post(
        "/api/v1/games/question-words/round",
        headers=headers,
        json={"mistakes": 2, "seconds": 41.6},
    )
    assert resp.status_code == 200

    qw = next(
        s
        for s in (await client.get("/api/v1/games", headers=headers)).json()["sets"]
        if s["slug"] == "question-words"
    )
    assert qw["played"] == 1
    assert qw["best"] == {"mistakes": 2, "seconds": 41.6}


async def test_the_round_reports_whether_it_is_a_new_best(
    client: AsyncClient,
) -> None:
    """Michael asked for a "new best" indicator on the finished board.

    The server answers it, because the tiebreak and the rounding are both
    here; a UI re-deriving either would drift out of step with the best score
    shown on the set list.
    """
    headers = await login(client)

    async def play(mistakes: int, seconds: float) -> dict:
        resp = await client.post(
            "/api/v1/games/question-words/round",
            headers=headers,
            json={"mistakes": mistakes, "seconds": seconds},
        )
        assert resp.status_code == 200
        return resp.json()

    # The first round beats nothing. `best` is true -- it is the best there
    # is -- but `previous` is null, which is how the UI knows not to
    # celebrate beating a record that did not exist.
    first = await play(2, 40.0)
    assert first["best"] is True
    assert first["previous"] is None

    worse = await play(3, 10.0)
    assert worse["best"] is False
    assert worse["previous"] == {"mistakes": 2, "seconds": 40.0}

    better = await play(0, 55.0)
    assert better["best"] is True
    assert better["previous"] == {"mistakes": 2, "seconds": 40.0}

    # Quicker at the same accuracy is still an improvement.
    quicker = await play(0, 30.0)
    assert quicker["best"] is True
    assert quicker["previous"] == {"mistakes": 0, "seconds": 55.0}


async def test_matching_your_own_best_is_not_beating_it(
    client: AsyncClient,
) -> None:
    headers = await login(client)
    for _ in range(2):
        resp = await client.post(
            "/api/v1/games/question-words/round",
            headers=headers,
            json={"mistakes": 1, "seconds": 20.0},
        )
    assert resp.json()["best"] is False
    assert resp.json()["previous"] == {"mistakes": 1, "seconds": 20.0}


async def test_a_new_best_is_judged_per_set_and_per_user(
    client: AsyncClient,
) -> None:
    """A round on one set must not be measured against another set's best."""
    headers = await login(client)
    await client.post(
        "/api/v1/games/question-words/round",
        headers=headers,
        json={"mistakes": 0, "seconds": 5.0},
    )
    async with db.session_factory() as session:
        session.add(
            GameSet(
                slug="other-set",
                language_code="nb",
                game="matching",
                title="Other",
                description="Another set entirely.",
                seq=99,
            )
        )
        await session.commit()

    resp = await client.post(
        "/api/v1/games/other-set/round",
        headers=headers,
        json={"mistakes": 9, "seconds": 300.0},
    )
    body = resp.json()
    assert body["best"] is True, "the first round on a set has nothing to beat"
    assert body["previous"] is None


async def test_fewer_mistakes_beats_a_quicker_round(client: AsyncClient) -> None:
    """Accuracy first: a fast round full of wrong guesses is not a better one."""
    headers = await login(client)
    for mistakes, seconds in [(2, 20.0), (0, 95.0), (5, 9.0)]:
        await client.post(
            "/api/v1/games/question-words/round",
            headers=headers,
            json={"mistakes": mistakes, "seconds": seconds},
        )
    qw = next(
        s
        for s in (await client.get("/api/v1/games", headers=headers)).json()["sets"]
        if s["slug"] == "question-words"
    )
    assert qw["played"] == 3
    assert qw["best"] == {"mistakes": 0, "seconds": 95.0}


async def test_a_tie_on_mistakes_goes_to_the_quicker_round(
    client: AsyncClient,
) -> None:
    headers = await login(client)
    for seconds in (60.0, 31.4, 48.0):
        await client.post(
            "/api/v1/games/question-words/round",
            headers=headers,
            json={"mistakes": 0, "seconds": seconds},
        )
    qw = next(
        s
        for s in (await client.get("/api/v1/games", headers=headers)).json()["sets"]
        if s["slug"] == "question-words"
    )
    assert qw["best"] == {"mistakes": 0, "seconds": 31.4}


async def test_a_round_records_how_big_the_set_was(client: AsyncClient) -> None:
    """So an old round stays readable after a set is edited."""
    headers = await login(client)
    await client.post(
        "/api/v1/games/question-words/round",
        headers=headers,
        json={"mistakes": 0, "seconds": 30.0},
    )
    async with db.session_factory() as session:
        row = await session.scalar(select(GameRound))
        assert row is not None and row.pairs == 10


async def test_nonsense_is_refused(client: AsyncClient) -> None:
    headers = await login(client)
    bad = await client.post(
        "/api/v1/games/question-words/round",
        headers=headers,
        json={"mistakes": -1, "seconds": 10.0},
    )
    assert bad.status_code == 422
    bad = await client.post(
        "/api/v1/games/question-words/round",
        headers=headers,
        json={"mistakes": 0, "seconds": 0},
    )
    assert bad.status_code == 422
    missing = await client.post(
        "/api/v1/games/no-such-set/round",
        headers=headers,
        json={"mistakes": 0, "seconds": 10.0},
    )
    assert missing.status_code == 404


async def test_games_need_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/games")).status_code == 401


async def test_one_learner_s_best_is_not_another_s(client: AsyncClient) -> None:
    from app.auth import hash_password
    from app.models import User

    async with db.session_factory() as session:
        session.add(
            User(
                email="player2@langkey.dev",
                display_name="Other",
                password_hash=hash_password("other-password"),
            )
        )
        await session.commit()

    headers = await login(client)
    await client.post(
        "/api/v1/games/question-words/round",
        headers=headers,
        json={"mistakes": 0, "seconds": 20.0},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "player2@langkey.dev", "password": "other-password"},
    )
    other = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    qw = next(
        s
        for s in (await client.get("/api/v1/games", headers=other)).json()["sets"]
        if s["slug"] == "question-words"
    )
    assert qw["best"] is None and qw["played"] == 0


async def test_resync_keeps_set_ids_so_rounds_survive_an_edit(
    client: AsyncClient,
) -> None:
    headers = await login(client)
    await client.post(
        "/api/v1/games/question-words/round",
        headers=headers,
        json={"mistakes": 1, "seconds": 33.0},
    )
    async with db.session_factory() as session:
        before = await session.scalar(
            select(GameSet).where(GameSet.slug == "question-words")
        )
        assert before is not None
        original_id = before.id

        entries = load_games("nb")
        entries[0]["title"] = "Question words (edited)"
        entries[0]["pairs"] = entries[0]["pairs"][:4]
        await _sync_games(session, "nb", entries)
        await session.commit()

        after = await session.scalar(
            select(GameSet).where(GameSet.slug == "question-words")
        )
        assert after is not None
        assert after.id == original_id, "the row was replaced, not updated"
        assert after.title == "Question words (edited)"
        pairs = list(
            await session.scalars(select(GamePair).where(GamePair.set_id == after.id))
        )
        assert len(pairs) == 4, "pairs were not rebuilt"

        rnd = await session.scalar(select(GameRound))
        assert rnd is not None and rnd.set_id == original_id


async def test_sync_is_a_no_op_when_the_content_has_not_changed(
    client: AsyncClient,
) -> None:
    async with db.session_factory() as session:
        assert await session.scalar(
            select(AppMeta).where(AppMeta.key == "games_hash:nb")
        ), "seeding should have recorded the content hash"
        await _sync_games(session, "nb", load_games("nb"))
        await session.commit()
        sets = list(await session.scalars(select(GameSet)))
        assert len(sets) == len(load_games("nb")), "a no-op sync duplicated rows"
