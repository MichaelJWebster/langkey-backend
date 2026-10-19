import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import audio, auth
from app.config import check_production_config, get_settings
from app.db import engine
from app.routes import (
    categories,
    exercises,
    games,
    glossary,
    hello,
    passages,
    progress,
    recordings,
    reviews,
    speaking,
    texts,
)
from app.glossary import backfill_glossary
from app.seed import seed_data
from app.startup import StartupGateMiddleware, prepare_database, readiness


def _run_migrations() -> None:
    cfg = Config(str(Path(__file__).parent.parent / "alembic.ini"))
    command.upgrade(cfg, "head")


async def _migrate() -> None:
    # Alembic's async env calls asyncio.run, so it needs its own thread.
    await asyncio.to_thread(_run_migrations)


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_production_config(get_settings())
    # Start serving straight away and prepare the database in the background (see
    # app/startup.py): a slow database must not stop the container from listening.
    readiness.mark_starting()
    preparing = asyncio.create_task(prepare_database([_migrate, seed_data, backfill_glossary]))
    yield
    preparing.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await preparing
    await engine.dispose()


app = FastAPI(title="LangKey API", version="0.2.0", lifespan=lifespan)

# Added before CORS so CORS wraps it, and 503s while starting up carry CORS headers too.
app.add_middleware(StartupGateMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(audio.router)
app.include_router(hello.router)
app.include_router(reviews.router)
app.include_router(progress.router)
app.include_router(categories.router)
app.include_router(glossary.router)
app.include_router(exercises.router)
app.include_router(texts.router)
app.include_router(passages.router)
app.include_router(games.router)
app.include_router(recordings.router)
app.include_router(speaking.router)


@app.get("/api/v1/health", tags=["health"])
async def health() -> dict[str, str]:
    # Always answers (it bypasses the startup gate); "database" is "starting" until
    # migrations and seeding have finished.
    return {"status": "ok", "database": readiness.status}
