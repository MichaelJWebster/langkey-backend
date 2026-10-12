"""A1 translation passages: the session queue, self-marking, and content sync.

The passages themselves arrive through the ordinary seed, so these tests run
against the real `data/a1_passages_nb.jsonl` rather than a fixture. That makes
them a gate on the content file too: a passage with an unglossed word or a
missing reference translation fails here.
"""

import re

import pytest
from httpx import AsyncClient
from sqlalchemy import select

import app.db as db
from app.content import load_passages
from app.models import AppMeta, Item, Passage, PassageAttempt
from app.seed import DEV_USER_EMAIL, _sync_passages
from tests.conftest import login


async def test_session_serves_the_most_familiar_passages_first(
    client: AsyncClient,
) -> None:
    """Michael: *"favouring sentences where I've seen more words"*.

    The number of words needing a hint is the difficulty that matters, so the
    queue is ordered by it. Authored sequence survives only as the tie-break.
    """
    headers = await login(client)
    body = (await client.get("/api/v1/passages", headers=headers)).json()

    assert body["total"] == len(load_passages("nb"))
    assert body["attempted"] == 0 and body["got"] == 0
    assert len(body["passages"]) == 10  # one session

    hints = [len(p["glossary"]) for p in body["passages"]]
    assert hints == sorted(hints), f"queue is not ordered by familiarity: {hints}"

    first = body["passages"][0]
    assert first["no"] and first["en"]
    assert first["last_mark"] is None and first["attempts"] == 0


async def test_a_familiar_passage_outranks_an_earlier_but_harder_one(
    client: AsyncClient,
) -> None:
    """a1-025 is authored last and needs the most hints; it must not lead.

    And the rule has to beat sequence, not merely agree with it: a1-003 is
    authored third yet needs almost nothing explained, so it comes before
    passages written before it.
    """
    headers = await login(client)
    served = [
        p["slug"]
        for p in (await client.get("/api/v1/passages", headers=headers)).json()[
            "passages"
        ]
    ]
    assert "a1-025" not in served, "the hardest passage led the queue"
    assert served.index("a1-003") < served.index("a1-001")


async def test_every_passage_carries_a_reference_and_its_glossary(
    client: AsyncClient,
) -> None:
    headers = await login(client)
    body = (
        await client.get("/api/v1/passages?limit=500", headers=headers)
    ).json()
    for p in body["passages"]:
        assert p["no"].strip(), p["slug"]
        assert p["en"].strip(), p["slug"]
        assert p["topics"], f"{p['slug']} has no topic and cannot be filtered"
        for g in p["glossary"]:
            # The form must be the one used in the Norwegian: a dictionary
            # form here is the bug this feature exists to avoid.
            assert g["form"].lower() in p["no"].lower(), (p["slug"], g["form"])
            assert g["en"].strip()


async def test_marking_a_passage_moves_it_down_the_queue(client: AsyncClient) -> None:
    headers = await login(client)
    first = (await client.get("/api/v1/passages", headers=headers)).json()[
        "passages"
    ][0]["slug"]

    resp = await client.post(
        f"/api/v1/passages/{first}/attempt",
        headers=headers,
        json={"mark": "got", "answer": "My name is Anna."},
    )
    assert resp.status_code == 200
    assert resp.json() == {"slug": first, "mark": "got", "score": 2}

    body = (await client.get("/api/v1/passages", headers=headers)).json()
    assert body["attempted"] == 1 and body["got"] == 1
    assert body["passages"][0]["slug"] != first, "a passage marked 'got' goes last"


async def test_a_missed_passage_comes_back_before_an_unseen_one(
    client: AsyncClient,
) -> None:
    headers = await login(client)
    slugs = [
        p["slug"]
        for p in (await client.get("/api/v1/passages?limit=500", headers=headers))
        .json()["passages"]
    ]
    # Work through the first three: two understood, one not.
    for slug, mark in zip(slugs, ["got", "got", "missed"]):
        await client.post(
            f"/api/v1/passages/{slug}/attempt", headers=headers, json={"mark": mark}
        )

    queue = [
        p["slug"]
        for p in (await client.get("/api/v1/passages", headers=headers)).json()[
            "passages"
        ]
    ]
    assert queue[0] == slugs[2], "the one he missed leads the next session"
    # ...but only a few review slots, so the session still moves forward.
    assert queue[1:4] == slugs[3:6], "after the review it carries on with new material"
    assert slugs[0] not in queue and slugs[1] not in queue


async def test_the_answer_is_kept_with_the_mark(client: AsyncClient) -> None:
    headers = await login(client)
    slug = (await client.get("/api/v1/passages", headers=headers)).json()[
        "passages"
    ][0]["slug"]
    await client.post(
        f"/api/v1/passages/{slug}/attempt",
        headers=headers,
        json={"mark": "partly", "answer": "I am called Anna, I am from Spain", "revealed": True},
    )
    async with db.session_factory() as session:
        row = await session.scalar(select(PassageAttempt))
        assert row is not None
        assert row.mark == "partly"
        assert row.answer == "I am called Anna, I am from Spain"
        assert row.revealed is True


async def test_an_invented_mark_is_refused(client: AsyncClient) -> None:
    headers = await login(client)
    slug = (await client.get("/api/v1/passages", headers=headers)).json()[
        "passages"
    ][0]["slug"]
    resp = await client.post(
        f"/api/v1/passages/{slug}/attempt", headers=headers, json={"mark": "brilliant"}
    )
    assert resp.status_code == 422
    resp = await client.post(
        "/api/v1/passages/no-such-slug/attempt", headers=headers, json={"mark": "got"}
    )
    assert resp.status_code == 404


async def test_topic_filter_narrows_the_session(client: AsyncClient) -> None:
    headers = await login(client)
    body = (
        await client.get("/api/v1/passages?topic=food&limit=500", headers=headers)
    ).json()
    assert body["total"] > 0
    assert all("food" in p["topics"] for p in body["passages"])


async def test_passages_need_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/passages")).status_code == 401


async def test_resync_keeps_passage_ids_so_attempts_survive_an_edit(
    client: AsyncClient,
) -> None:
    """A content edit must not orphan the learner's history.

    Replacing rows instead of updating them is what broke startup when the
    deck grew (see `_sync_examples`); the same trap is here, because
    `passage_attempts.passage_id` points at these ids.
    """
    headers = await login(client)
    slug = (await client.get("/api/v1/passages", headers=headers)).json()[
        "passages"
    ][0]["slug"]
    await client.post(
        f"/api/v1/passages/{slug}/attempt", headers=headers, json={"mark": "got"}
    )

    async with db.session_factory() as session:
        before = await session.scalar(select(Passage).where(Passage.slug == slug))
        assert before is not None
        original_id = before.id

        # Edit the content: change the reference translation and add a gloss.
        # The entry to edit is found by slug -- the queue no longer serves the
        # content file in order, so entries[0] is not what was served first.
        entries = load_passages("nb")
        edited = next(e for e in entries if e["slug"] == slug)
        edited["en"] = "A different reference translation."
        edited["glossary"] = [
            {"form": edited["no"].split()[0], "lemma": "x", "en": "x"}
        ]
        await session.execute(
            select(AppMeta).where(AppMeta.key == "passages_hash:nb")
        )
        await _sync_passages(session, "nb", entries)
        await session.commit()

        after = await session.scalar(select(Passage).where(Passage.slug == slug))
        assert after is not None
        assert after.id == original_id, "the row was replaced, not updated"
        assert after.en_text == "A different reference translation."
        assert [g.form for g in after.glosses] == [edited["no"].split()[0]]

        attempt = await session.scalar(select(PassageAttempt))
        assert attempt is not None
        assert attempt.passage_id == original_id, "the attempt lost its passage"


async def test_sync_is_a_no_op_when_the_content_has_not_changed(
    client: AsyncClient,
) -> None:
    async with db.session_factory() as session:
        entries = load_passages("nb")
        before = await session.scalar(
            select(AppMeta).where(AppMeta.key == "passages_hash:nb")
        )
        assert before is not None, "seeding should have recorded the content hash"
        await _sync_passages(session, "nb", entries)
        await session.commit()
        rows = list(await session.scalars(select(Passage)))
        assert len(rows) == len(entries), "a no-op sync duplicated rows"


# --- Glossing words the learner has not met yet ------------------------------
#
# Michael, trying the first batch: *"I notice there are words that I haven't
# seen appearing in the sentences, though they might be in the A1 vocabulary.
# Could we add hints/translations for those words I haven't seen alongside the
# translations for words outside the vocabulary?"*
#
# So "unfamiliar" is per learner, not a property of the passage: being in the
# course is no help while reading if it has not been taught yet.


async def mark_seen(user_email: str, words: list[str]) -> None:
    """Record that this learner has encountered these deck words."""
    from datetime import UTC, datetime

    from app.models import User, UserGlossaryEntry

    async with db.session_factory() as session:
        user = await session.scalar(select(User).where(User.email == user_email))
        assert user is not None
        for word in words:
            item = await session.scalar(select(Item).where(Item.source_text == word))
            assert item is not None, f"{word!r} is not in the deck"
            session.add(
                UserGlossaryEntry(
                    user_id=user.id,
                    item_id=item.id,
                    first_seen_at=datetime.now(UTC),
                    via="card",
                )
            )
        await session.commit()


async def glossary_for(client: AsyncClient, headers: dict[str, str], slug: str):
    body = (await client.get("/api/v1/passages?limit=500", headers=headers)).json()
    for p in body["passages"]:
        if p["slug"] == slug:
            return p["glossary"]
    raise AssertionError(f"{slug} not served")


async def test_a_deck_word_not_yet_met_is_glossed(client: AsyncClient) -> None:
    headers = await login(client)
    gloss = await glossary_for(client, headers, "a1-009")  # "Jeg liker å lese bøker..."
    forms = {g["form"].lower(): g for g in gloss}

    assert "bøker" in forms, "a deck word he has not met must be glossed"
    assert forms["bøker"]["source"] == "unseen"
    assert forms["bøker"]["lemma"] == "bøker"
    assert forms["bøker"]["en"]


async def test_a_word_already_met_is_not_glossed(client: AsyncClient) -> None:
    headers = await login(client)
    before = await glossary_for(client, headers, "a1-009")
    assert any(g["form"].lower() == "bøker" for g in before)

    await mark_seen(DEV_USER_EMAIL, ["bøker"])

    after = await glossary_for(client, headers, "a1-009")
    assert not any(g["form"].lower() == "bøker" for g in after), (
        "a word he has met is no longer a hint, it is vocabulary"
    )


async def test_the_two_kinds_of_unfamiliar_word_sit_together(
    client: AsyncClient,
) -> None:
    """a1-016 has an authored gloss (`snør`, not in the deck) and deck words."""
    headers = await login(client)
    gloss = await glossary_for(client, headers, "a1-016")
    by_form = {g["form"].lower(): g for g in gloss}

    assert by_form["snør"]["source"] == "new", "å snø is not in the deck at all"
    unseen = [g for g in gloss if g["source"] == "unseen"]
    assert unseen, "the deck words in this passage have not been met either"
    # Both kinds, one list, each marked.
    assert {g["source"] for g in gloss} == {"new", "unseen"}


async def test_the_glossary_reads_in_the_order_the_words_appear(
    client: AsyncClient,
) -> None:
    headers = await login(client)
    gloss = await glossary_for(client, headers, "a1-016")
    body = (await client.get("/api/v1/passages?limit=500", headers=headers)).json()
    no = next(p["no"] for p in body["passages"] if p["slug"] == "a1-016").lower()

    # Word-boundary search, not a plain substring one: "er" occurs inside
    # "vinteren" long before it appears as a word, and the route positions a
    # matched token, not the first place its letters happen to turn up.
    def at(form: str) -> int:
        m = re.search(rf"\b{re.escape(form.lower())}\b", no)
        return m.start() if m else no.find(form.lower())

    offsets = [at(g["form"]) for g in gloss]
    assert offsets == sorted(offsets), (
        f"glossary out of reading order: {[(g['form'], o) for g, o in zip(gloss, offsets)]}"
    )


async def test_an_authored_note_wins_over_the_deck_translation(
    client: AsyncClient,
) -> None:
    """a1-017 glosses `en time` as "an appointment" -- the deck says "an hour".

    The authored note was written for this passage and knows which sense is
    meant, so it must not be replaced by the dictionary entry.
    """
    headers = await login(client)
    gloss = await glossary_for(client, headers, "a1-017")
    time = [g for g in gloss if g["form"].lower() == "en time"]
    assert len(time) == 1, "glossed twice"
    assert time[0]["source"] == "new"
    assert "appointment" in time[0]["en"]


async def test_glossing_does_not_leak_between_learners(client: AsyncClient) -> None:
    """The glossary is per learner: one person's progress is not another's."""
    from app.auth import hash_password
    from app.models import User

    async with db.session_factory() as session:
        session.add(
            User(
                email="other@langkey.dev",
                display_name="Other",
                password_hash=hash_password("other-password"),
            )
        )
        await session.commit()

    await mark_seen(DEV_USER_EMAIL, ["bøker"])
    dev = await login(client)
    assert not any(
        g["form"].lower() == "bøker" for g in await glossary_for(client, dev, "a1-009")
    )

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "other@langkey.dev", "password": "other-password"},
    )
    other = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    assert any(
        g["form"].lower() == "bøker"
        for g in await glossary_for(client, other, "a1-009")
    ), "the other learner has met nothing, so they still get the hint"


# --- Exchanges laid out as a question and its answer --------------------------
#
# Michael: *"It would be better if we formatted it like this: Question:
# Unnskyld, hvor er nærmeste bank? / Answer: Den ligger rett over gata, ved
# siden av apoteket."*


async def test_an_exchange_is_served_as_labelled_lines(client: AsyncClient) -> None:
    headers = await login(client)
    gloss_free = (
        await client.get("/api/v1/passages?limit=500", headers=headers)
    ).json()
    bank = next(p for p in gloss_free["passages"] if p["slug"] == "a1-007")

    assert bank["lines"] is not None
    assert [ln["role"] for ln in bank["lines"]] == ["question", "answer"]
    assert bank["lines"][0]["no"] == "Unnskyld, hvor er nærmeste bank?"
    assert bank["lines"][1]["no"] == "Den ligger rett over gata, ved siden av apoteket."
    # The English is split the same way, so the learner can compare line by line.
    assert bank["lines"][0]["en"] == "Excuse me, where is the nearest bank?"


async def test_a_plain_passage_has_no_lines(client: AsyncClient) -> None:
    headers = await login(client)
    body = (await client.get("/api/v1/passages?limit=500", headers=headers)).json()
    anna = next(p for p in body["passages"] if p["slug"] == "a1-001")
    assert anna["lines"] is None


async def test_the_whole_text_is_derived_from_the_lines() -> None:
    """Two copies of the same sentence drift apart.

    `no_text` is what the gates, the word count and the glossary run on, so if
    it were authored separately from the lines a drift would mean gating text
    the learner never sees. It is joined from the lines instead.
    """
    for e in load_passages("nb"):
        if not e["lines"]:
            continue
        assert e["no"] == " ".join(ln["no"] for ln in e["lines"])
        assert e["en"] == " ".join(ln["en"] for ln in e["lines"])


async def test_every_line_has_a_known_role() -> None:
    from app.models import ROLES

    for e in load_passages("nb"):
        for ln in e["lines"] or ():
            assert ln["role"] in ROLES, (e["slug"], ln["role"])
            assert ln["no"].strip() and ln["en"].strip(), e["slug"]


async def test_the_glossary_still_covers_the_whole_exchange(
    client: AsyncClient,
) -> None:
    """Glossing runs on the joined text, so a word in either line is covered."""
    headers = await login(client)
    gloss = await glossary_for(client, headers, "a1-007")
    forms = {g["form"].lower() for g in gloss}
    # "apoteket" is in the answer, "nærmeste" in the question.
    assert any("apotek" in f for f in forms), forms


# --- The standing, not just the session ---------------------------------------
#
# Michael, after a run of exercises: *"I'm not seeing a percentage or score
# appear on the main page of the application, or in the reading exercises
# section. Note: the scores are only for the sentences we created for
# exercises."*


async def test_tally_is_null_before_anything_is_marked(client: AsyncClient) -> None:
    headers = await login(client)
    tally = (await client.get("/api/v1/passages", headers=headers)).json()["tally"]
    assert tally["attempted"] == 0
    assert tally["score"] is None, "a zero here would read as 'you scored nothing'"
    assert tally["total"] == len(load_passages("nb"))


async def test_a_partial_counts_as_a_half(client: AsyncClient) -> None:
    headers = await login(client)
    slugs = [
        p["slug"]
        for p in (await client.get("/api/v1/passages?limit=500", headers=headers))
        .json()["passages"]
    ]
    for slug, mark in zip(slugs, ["got", "partly", "missed", "got"]):
        await client.post(
            f"/api/v1/passages/{slug}/attempt", headers=headers, json={"mark": mark}
        )

    tally = (await client.get("/api/v1/passages", headers=headers)).json()["tally"]
    assert (tally["got"], tally["partly"], tally["missed"]) == (2, 1, 1)
    assert tally["attempted"] == 4
    # (1 + 0.5 + 0 + 1) / 4
    assert tally["score"] == 62.5


async def test_the_score_is_over_what_was_attempted_not_the_whole_set(
    client: AsyncClient,
) -> None:
    """Averaging over 200 passages would read as 1% to someone doing well."""
    headers = await login(client)
    slug = (await client.get("/api/v1/passages", headers=headers)).json()[
        "passages"
    ][0]["slug"]
    await client.post(
        f"/api/v1/passages/{slug}/attempt", headers=headers, json={"mark": "got"}
    )
    tally = (await client.get("/api/v1/passages", headers=headers)).json()["tally"]
    assert tally["score"] == 100.0
    assert tally["attempted"] == 1 and tally["total"] > 1


async def test_only_the_latest_mark_counts(client: AsyncClient) -> None:
    """Going back and getting it right replaces the miss, not averages with it."""
    headers = await login(client)
    slug = (await client.get("/api/v1/passages", headers=headers)).json()[
        "passages"
    ][0]["slug"]
    for mark in ("missed", "partly", "got"):
        await client.post(
            f"/api/v1/passages/{slug}/attempt", headers=headers, json={"mark": mark}
        )
    tally = (await client.get("/api/v1/passages", headers=headers)).json()["tally"]
    assert tally["attempted"] == 1
    assert (tally["got"], tally["partly"], tally["missed"]) == (1, 0, 0)
    assert tally["score"] == 100.0


async def test_the_same_tally_reaches_the_dashboard(client: AsyncClient) -> None:
    headers = await login(client)
    slugs = [
        p["slug"]
        for p in (await client.get("/api/v1/passages", headers=headers)).json()[
            "passages"
        ]
    ]
    for slug, mark in zip(slugs, ["got", "missed"]):
        await client.post(
            f"/api/v1/passages/{slug}/attempt", headers=headers, json={"mark": mark}
        )

    reading = (await client.get("/api/v1/progress/skills", headers=headers)).json()[
        "reading"
    ]
    assert reading["passages"]["score"] == 50.0
    assert reading["passages"]["attempted"] == 2
    # The library is read, not scored: it keeps its own counters and gains no
    # percentage from this.
    assert "sentences_read" in reading and "score" not in reading


async def test_one_learner_s_score_is_not_another_s(client: AsyncClient) -> None:
    from app.auth import hash_password
    from app.models import User

    async with db.session_factory() as session:
        session.add(
            User(
                email="other2@langkey.dev",
                display_name="Other",
                password_hash=hash_password("other-password"),
            )
        )
        await session.commit()

    headers = await login(client)
    slug = (await client.get("/api/v1/passages", headers=headers)).json()[
        "passages"
    ][0]["slug"]
    await client.post(
        f"/api/v1/passages/{slug}/attempt", headers=headers, json={"mark": "got"}
    )

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "other2@langkey.dev", "password": "other-password"},
    )
    other = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    tally = (await client.get("/api/v1/passages", headers=other)).json()["tally"]
    assert tally["attempted"] == 0 and tally["score"] is None
