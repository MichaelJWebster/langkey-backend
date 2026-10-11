"""A1 translation passages: the session queue, self-marking, and content sync.

The passages themselves arrive through the ordinary seed, so these tests run
against the real `data/a1_passages_nb.jsonl` rather than a fixture. That makes
them a gate on the content file too: a passage with an unglossed word or a
missing reference translation fails here.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

import app.db as db
from app.content import load_passages
from app.models import AppMeta, Passage, PassageAttempt
from app.seed import _sync_passages, seed_data
from tests.conftest import login


async def test_session_serves_passages_easiest_first(client: AsyncClient) -> None:
    headers = await login(client)
    body = (await client.get("/api/v1/passages", headers=headers)).json()

    assert body["total"] == len(load_passages("nb"))
    assert body["attempted"] == 0 and body["got"] == 0
    assert len(body["passages"]) == 10  # one session
    assert [p["slug"] for p in body["passages"]] == sorted(
        p["slug"] for p in body["passages"]
    ), "authored order is easiest-first; the queue must not shuffle it"

    first = body["passages"][0]
    assert first["no"] and first["en"]
    assert first["last_mark"] is None and first["attempts"] == 0


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
        entries = load_passages("nb")
        entries[0]["en"] = "A different reference translation."
        entries[0]["glossary"] = [{"form": "Anna", "lemma": "Anna", "en": "Anna"}]
        await session.execute(
            select(AppMeta).where(AppMeta.key == "passages_hash:nb")
        )
        await _sync_passages(session, "nb", entries)
        await session.commit()

        after = await session.scalar(select(Passage).where(Passage.slug == slug))
        assert after is not None
        assert after.id == original_id, "the row was replaced, not updated"
        assert after.en_text == "A different reference translation."
        assert [g.form for g in after.glosses] == ["Anna"]

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
