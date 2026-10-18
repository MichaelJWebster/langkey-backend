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
    assert set(skills) == {
        "vocabulary", "listening", "reading", "writing", "speaking", "conversation",
    }
    assert not skills["speaking"]["practised"]
    assert not skills["conversation"]["practised"]
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


# --- speaking (ADR-044) -------------------------------------------------------

import uuid  # noqa: E402

import app.db as db  # noqa: E402
from app.models import Item, PronunciationAttempt, User, UserRecording  # noqa: E402
from app.routes.progress import SPEAKING_SATURATION, SPEAKING_WINDOW  # noqa: E402
from sqlalchemy import select  # noqa: E402


async def _speak(takes: int, words_per_take: int, said_rate: float) -> None:
    """Record checked takes for the dev user, each with its word marks."""
    async with db.session_factory() as session:
        user = await session.scalar(select(User).where(User.email == "dev@langkey.dev"))
        item = await session.scalar(select(Item).where(Item.language_code == "nb").limit(1))
        for t in range(takes):
            rec = UserRecording(
                id=uuid.uuid4(),
                user_id=user.id,
                item_id=item.id,
                object_key=f"recordings/test/{uuid.uuid4()}.webm",
                content_type="audio/webm",
                size_bytes=1,
                transcript="x",
                transcript_model="fake",
            )
            session.add(rec)
            await session.flush()
            hits = round(words_per_take * said_rate)
            session.add_all(
                PronunciationAttempt(
                    user_id=user.id, recording_id=rec.id, language_code="nb",
                    word=f"w{w}", said=w < hits,
                )
                for w in range(words_per_take)
            )
        await session.commit()


async def _speaking_skill(client, headers) -> dict:
    body = (await client.get("/api/v1/progress/cefr", headers=headers)).json()
    return {s["skill"]: s for s in body["skills"]}["speaking"]


async def test_speaking_says_so_when_nothing_has_been_checked(client: AsyncClient) -> None:
    headers = await login(client)
    speaking = await _speaking_skill(client, headers)
    assert speaking["practised"] is False
    assert speaking["detail"] == "no pronunciation practice yet"
    assert speaking["progress"] == 0.0


async def test_speaking_counts_once_takes_are_checked(client: AsyncClient) -> None:
    """Michael: "I notice we have no percent complete showing for speaking
    yet. The front page is showing 'not trained in Langkey yet'." """
    headers = await login(client)
    await _speak(takes=4, words_per_take=5, said_rate=0.8)

    speaking = await _speaking_skill(client, headers)

    assert speaking["practised"] is True
    assert speaking["detail"] == "80% of recent words heard clearly · 4 phrases checked"


async def test_clarity_is_taken_over_recent_words_only(client: AsyncClient) -> None:
    # An early run of misses must age out as he improves: the window is the
    # last SPEAKING_WINDOW words, not the whole history.
    headers = await login(client)
    await _speak(takes=SPEAKING_WINDOW // 5, words_per_take=5, said_rate=0.0)
    await _speak(takes=SPEAKING_WINDOW // 5, words_per_take=5, said_rate=1.0)

    speaking = await _speaking_skill(client, headers)

    assert speaking["detail"].startswith("100% of recent words")


def test_the_speaking_constants_say_what_the_adr_says() -> None:
    assert SPEAKING_WINDOW == 200
    assert SPEAKING_SATURATION == 100


async def test_conversation_is_reported_but_not_yet_estimated(client: AsyncClient) -> None:
    """Michael: "Perhaps we should split out the unimplemented conversation
    part of speaking into a new conversation section." CEFR splits spoken
    production from spoken interaction; pronunciation trains the first."""
    headers = await login(client)
    await _speak(takes=SPEAKING_SATURATION, words_per_take=2, said_rate=1.0)

    body = (await client.get("/api/v1/progress/cefr", headers=headers)).json()
    skills = {s["skill"]: s for s in body["skills"]}

    assert [s["skill"] for s in body["skills"]][-2:] == ["speaking", "conversation"]
    assert skills["conversation"] == {
        "skill": "conversation",
        "practised": False,
        "level": None,
        "next_level": "A1",
        "progress": 0.0,
        "detail": "not trained in LangKey yet",
    }
    # Pronunciation practice moves speaking, never conversation.
    assert skills["speaking"]["practised"] is True
