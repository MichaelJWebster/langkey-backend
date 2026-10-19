"""backfill_glossary is a one-off: it runs once per database, then boots skip it."""

from httpx import AsyncClient
from sqlalchemy import event

import app.db as db
import app.glossary as glossary
from app.models import AppMeta


async def test_backfill_runs_once_then_is_skipped(client: AsyncClient) -> None:
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        statements.append(statement.split()[0].upper())

    event.listen(db.engine.sync_engine, "before_cursor_execute", record)
    try:
        await glossary.backfill_glossary()
        first_run = list(statements)
        statements.clear()
        await glossary.backfill_glossary()
        second_run = list(statements)
    finally:
        event.remove(db.engine.sync_engine, "before_cursor_execute", record)

    async with db.session_factory() as session:
        assert await session.get(AppMeta, glossary.BACKFILL_DONE_KEY) is not None

    # First run: marker lookup, the review-history query, then the marker insert.
    assert first_run.count("SELECT") >= 2 and "INSERT" in first_run
    # Second run: only the marker lookup, so a cold boot no longer walks the history.
    assert second_run == ["SELECT"]
