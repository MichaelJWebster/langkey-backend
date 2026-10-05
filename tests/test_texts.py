"""Reading library API: list, paged interleaved fetch, resume position."""

import json

import pytest
from httpx import AsyncClient

import app.db as db
from app.models import ReadingText, Sentence, SentenceTranslation, TextSection
from tests.conftest import login


async def seed_text(*, title_en: str | None = "Test Tale in English") -> None:
    async with db.session_factory() as session:
        text = ReadingText(
            slug="test-tale", title="Test Tale", author="A. Author",
            title_en=title_en,
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


async def run_import(staging, monkeypatch, **kwargs) -> None:
    """Call scripts.import_text against the test database.

    The script does `from app.db import session_factory`, which binds whatever
    engine existed when the module was first imported -- the previous test's,
    by the time a second test runs. Point it at the live one.
    """
    import scripts.import_text as import_text_module

    monkeypatch.setattr(import_text_module, "session_factory", db.session_factory)
    await import_text_module.import_text(staging, **kwargs)


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


@pytest.mark.anyio
async def test_english_title_is_returned_in_both_shapes(client: AsyncClient) -> None:
    # The learner meets the title twice -- choosing a text, and at the top of
    # the reader -- so both endpoints carry it.
    await seed_text()
    headers = await login(client)

    listing = (await client.get("/api/v1/texts", headers=headers)).json()
    assert listing[0]["title"] == "Test Tale"
    assert listing[0]["title_en"] == "Test Tale in English"

    page = (await client.get("/api/v1/texts/test-tale", headers=headers)).json()
    assert page["title_en"] == "Test Tale in English"


@pytest.mark.anyio
async def test_a_text_without_an_english_title_still_lists(client: AsyncClient) -> None:
    # Null is the honest answer when the gates rejected the translation; the
    # UI shows the Norwegian alone rather than inventing one.
    await seed_text(title_en=None)
    headers = await login(client)

    listing = (await client.get("/api/v1/texts", headers=headers)).json()
    assert listing[0]["title_en"] is None


@pytest.mark.anyio
async def test_reimport_keeps_a_hand_corrected_english_title(
    client: AsyncClient, tmp_path, monkeypatch
) -> None:
    # The model proposed "The Wife" for Kveita, which means halibut, and the
    # correction was made straight in the database. Staging files carry only
    # what the model proposed -- nothing, for anything imported before titles
    # were translated -- so a re-import must not undo the fix.
    await seed_text(title_en="The Halibut")
    staging = tmp_path / "test-tale.json"
    staging.write_text(
        json.dumps(
            {
                "slug": "test-tale",
                "title": "Test Tale",
                "author": "A. Author",
                "language": "nb",
                "kind": "folktale",
                "sections": [{"seq": 1, "heading": None}],
                "sentences": [
                    {"seq": 1, "section": 1, "paragraph": 1, "no": "Setning 1.", "en": "Sentence 1."}
                ],
            }
        )
    )

    await run_import(staging, monkeypatch, replace=True)

    async with db.session_factory() as session:
        text = await session.scalar(
            __import__("sqlalchemy").select(ReadingText).where(ReadingText.slug == "test-tale")
        )
        assert text is not None
        assert text.title_en == "The Halibut"


@pytest.mark.anyio
async def test_staging_english_title_wins_when_it_has_one(
    client: AsyncClient, tmp_path, monkeypatch
) -> None:
    await seed_text(title_en="Stale title")
    staging = tmp_path / "test-tale.json"
    staging.write_text(
        json.dumps(
            {
                "slug": "test-tale",
                "title": "Test Tale",
                "title_en": "Fresh title",
                "language": "nb",
                "kind": "folktale",
                "sections": [{"seq": 1, "heading": None}],
                "sentences": [
                    {"seq": 1, "section": 1, "paragraph": 1, "no": "Setning 1.", "en": "Sentence 1."}
                ],
            }
        )
    )

    await run_import(staging, monkeypatch, replace=True)

    async with db.session_factory() as session:
        text = await session.scalar(
            __import__("sqlalchemy").select(ReadingText).where(ReadingText.slug == "test-tale")
        )
        assert text is not None
        assert text.title_en == "Fresh title"
