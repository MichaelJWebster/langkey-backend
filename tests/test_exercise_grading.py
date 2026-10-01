"""Hinted answers earn a graded mark that drives speed pacing and resampling.

An attempt's score is the share of the sentence produced without hints
(0 when wrong). Rows from before grading fall back to the old binary rule:
full credit only for hint-free correct answers.
"""

from httpx import AsyncClient
from sqlalchemy import select

import app.db as db
from app.audio import DICTATION_SCALES
from app.models import User
from app.routes.exercises import _attempt_score, _practice_need, _weighted_sample
from tests.conftest import login


def test_attempt_score_falls_back_for_pre_grading_rows() -> None:
    assert _attempt_score(correct=True, hints=0, score=None) == 1.0
    assert _attempt_score(correct=True, hints=2, score=None) == 0.0
    assert _attempt_score(correct=False, hints=0, score=None) == 0.0
    assert _attempt_score(correct=True, hints=2, score=0.75) == 0.75


def test_weighted_sample_prefers_heavy_entries() -> None:
    pool = ["light", "heavy"]
    heavy = sum(_weighted_sample(pool, [1.0, 50.0], k=1)[0] == "heavy" for _ in range(200))
    assert heavy >= 150  # expectation ≈ 196 of 200
    assert sorted(_weighted_sample(pool, [1.0, 50.0], k=2)) == ["heavy", "light"]


async def _study_some_cards(client: AsyncClient, headers: dict) -> None:
    queue = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    for card in queue["cards"][:3]:
        await client.post(
            "/api/v1/reviews", json={"card_id": card["card_id"], "rating": 3}, headers=headers
        )


async def test_suggested_scale_uses_graded_marks(client: AsyncClient) -> None:
    headers = await login(client)
    await _study_some_cards(client, headers)

    # Twelve correct-but-hinted dictations marked 0.6. The old binary rule
    # (hint-free correct only) would read this as 0 accuracy and stay very
    # slow; the graded mark lands in the middle speed band.
    for _ in range(12):
        resp = await client.post(
            "/api/v1/exercises/attempts",
            json={"exercise_type": "dictation", "item_id": 1, "example_id": 1,
                  "correct": True, "hints": 1, "score": 0.6, "answer": "x"},
            headers=headers,
        )
        assert resp.status_code == 201

    body = (await client.get("/api/v1/exercises/listening?limit=8", headers=headers)).json()
    dictations = [e for e in body["exercises"] if e["kind"] == "dictation"]
    assert dictations
    assert all(e["suggested_scale"] == DICTATION_SCALES[1] for e in dictations)


async def test_practice_need_tracks_recent_marks(client: AsyncClient) -> None:
    headers = await login(client)
    await _study_some_cards(client, headers)

    for example_id, correct, score in ((1, False, 0.0), (2, True, 1.0)):
        resp = await client.post(
            "/api/v1/exercises/attempts",
            json={"exercise_type": "dictation", "item_id": 1, "example_id": example_id,
                  "correct": correct, "hints": 0, "score": score, "answer": "x"},
            headers=headers,
        )
        assert resp.status_code == 201

    async with db.session_factory() as session:
        user_id = (await session.scalars(select(User.id))).first()
        need = await _practice_need(session, user_id)
    assert need[1] == 1.0  # flubbed sentence wants another go
    assert need[2] == 0.0  # clean run returns to the ordinary rotation
