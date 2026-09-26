"""Tests run against an in-memory SQLite database instead of Postgres."""

import os

os.environ["LANGKEY_DATABASE_URL"] = "sqlite+aiosqlite://"

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db as db
from app.db import Base


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    db.engine = engine
    db.session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Imported after the engine swap so seeding uses the test session factory.
    import app.seed as seed
    from app.main import app as fastapi_app

    seed.session_factory = db.session_factory
    await seed.create_schema_and_seed(engine)

    async def _get_session():
        async with db.session_factory() as session:
            yield session

    fastapi_app.dependency_overrides[db.get_session] = _get_session
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    fastapi_app.dependency_overrides.clear()
    await engine.dispose()


async def login(client: AsyncClient) -> dict[str, str]:
    """Log in as the seeded dev user and return auth headers."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "dev@langkey.dev", "password": "langkey-dev"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}
