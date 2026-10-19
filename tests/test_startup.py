"""The server answers while the database is still being prepared (app/startup.py)."""

import asyncio
from collections.abc import Iterator

import pytest
from httpx import AsyncClient

import app.startup as startup
from app.startup import prepare_database, readiness


@pytest.fixture(autouse=True)
def restore_readiness() -> Iterator[None]:
    yield
    readiness.mark_ready()


async def test_api_gets_503_if_database_not_ready_in_time(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(startup, "GATE_WAIT_SECONDS", 0.05)
    readiness.mark_starting()

    resp = await client.get("/api/v1/hello")

    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "5"
    assert "starting up" in resp.json()["detail"]


async def test_health_answers_while_starting(client: AsyncClient) -> None:
    readiness.mark_starting()

    resp = await client.get("/api/v1/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "database": "starting"}


async def test_request_waits_for_database_then_proceeds(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(startup, "GATE_WAIT_SECONDS", 5.0)
    readiness.mark_starting()

    request = asyncio.create_task(client.get("/api/v1/hello"))
    await asyncio.sleep(0.05)
    assert not request.done()  # held at the gate, not failed
    readiness.mark_ready()

    resp = await request
    assert resp.status_code == 401  # reached the real endpoint, which needs a login


async def test_prepare_database_retries_until_steps_succeed() -> None:
    calls: list[str] = []
    failures_left = 2

    async def migrate() -> None:
        calls.append("migrate")

    async def seed() -> None:
        nonlocal failures_left
        calls.append("seed")
        if failures_left:
            failures_left -= 1
            raise ConnectionError("database is waking up")

    readiness.mark_starting()
    await prepare_database([migrate, seed], first_delay=0, max_delay=0)

    assert readiness.status == "ready"
    assert readiness.attempts == 3
    assert readiness.error is None
    assert calls == ["migrate", "seed"] * 3


async def test_prepare_database_records_error_and_stops_when_cancelled() -> None:
    async def always_fails() -> None:
        raise ConnectionError("no database")

    readiness.mark_starting()
    task = asyncio.create_task(prepare_database([always_fails], first_delay=10))
    await asyncio.sleep(0.05)

    assert readiness.status == "starting"
    assert readiness.error == "ConnectionError: no database"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
