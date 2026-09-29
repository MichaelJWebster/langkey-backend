"""Reading library API: list, paged interleaved fetch, resume position."""

import pytest
from httpx import AsyncClient

import app.db as db
from app.models import ReadingText, Sentence, SentenceTranslation, TextSection
from tests.conftest import login


async def seed_text() -> None:
    async with db.session_factory() as session:
        text = ReadingText(
            slug="test-tale", title="Test Tale", author="A. Author",
            language_code="nb", kind="folktale", orthography="dano-1904",
        )
        session.add(text)
        await session.flush()
        section = TextSection(text_id=text.id, seq=1)
        session.add(section)
        await session.flush()
        for i in range(1, 6):
            s = Sentence(
                text_id=text.id, section_id=section.id, seq=i,
                paragraph=1 + i // 3, no_text=f"Setning {i}.",
            )
            session.add(s)
            await session.flush()
            session.add(
                SentenceTranslation(sentence_id=s.id, translator="test", en_text=f"Sentence {i}.")
            )
        await session.commit()


@pytest.mark.anyio
async def test_texts_list_and_page(client: AsyncClient) -> None:
    await seed_text()
    headers = await login(client)

    listing = (await client.get("/api/v1/texts", headers=headers)).json()
    assert [t["slug"] for t in listing] == ["test-tale"]
    assert listing[0]["sentence_count"] == 5
    assert listing[0]["position"] == 0

    page = (await client.get("/api/v1/texts/test-tale?from_seq=2", headers=headers)).json()
    assert page["sentence_count"] == 5
    assert [s["seq"] for s in page["sentences"]] == [2, 3, 4, 5]
    assert page["sentences"][0] == {"seq": 2, "paragraph": 1, "no": "Setning 2.", "en": "Sentence 2."}


@pytest.mark.anyio
async def test_reading_position_persists_forward_only(client: AsyncClient) -> None:
    await seed_text()
    headers = await login(client)

    r = await client.put("/api/v1/texts/test-tale/position", json={"seq": 4}, headers=headers)
    assert r.json() == {"seq": 4}
    # implicit backwards move (re-reading) doesn't lose the resume point
    r = await client.put("/api/v1/texts/test-tale/position", json={"seq": 2}, headers=headers)
    assert r.json() == {"seq": 4}
    listing = (await client.get("/api/v1/texts", headers=headers)).json()
    assert listing[0]["position"] == 4

    assert (await client.get("/api/v1/texts/nope", headers=headers)).status_code == 404
