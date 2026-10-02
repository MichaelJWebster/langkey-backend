"""CEFR estimation: ladder maths and the per-skill progress endpoint."""

from httpx import AsyncClient

from app.cefr import estimate
from tests.conftest import login


def test_estimate_ladder() -> None:
    assert estimate(0) == (None, "A1", 0.0)
    level, nxt, prog = estimate(250)
    assert (level, nxt) == (None, "A1") and prog == 0.5
    assert estimate(500)[0] == "A1"
    level, nxt, prog = estimate(850)  # halfway from A1 (500) to A2 (1200)
    assert (level, nxt) == ("A1", "A2") and prog == 0.5
    assert estimate(9000) == ("C2", None, 1.0)


async def test_cefr_endpoint(client: AsyncClient) -> None:
    headers = await login(client)

    body = (await client.get("/api/v1/progress/cefr", headers=headers)).json()
    assert body["overall_level"] is None  # fresh account: pre-A1
    assert body["overall_next"] == "A1"
    skills = {s["skill"]: s for s in body["skills"]}
    assert set(skills) == {"vocabulary", "listening", "reading", "writing", "speaking"}
    assert not skills["speaking"]["practised"]
    assert skills["vocabulary"]["detail"] == "0 words established"
    assert "Norskprøven" in body["note"]

    # Reviewing cards doesn't establish them yet (FSRS learning state), so the
    # estimate stays pre-A1 but the endpoint keeps working with activity.
    queue = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    await client.post(
        "/api/v1/reviews",
        json={"card_id": queue["cards"][0]["card_id"], "rating": 4},
        headers=headers,
    )
    await client.post(
        "/api/v1/exercises/attempts",
        json={"exercise_type": "dictation", "item_id": 1, "example_id": 1,
              "correct": True, "hints": 0, "score": 1.0, "answer": "x"},
        headers=headers,
    )
    body = (await client.get("/api/v1/progress/cefr", headers=headers)).json()
    skills = {s["skill"]: s for s in body["skills"]}
    assert skills["listening"]["practised"]
    assert skills["listening"]["detail"] == "1 recent dictations at 100%"
    assert 0.0 <= body["overall_progress"] <= 1.0
