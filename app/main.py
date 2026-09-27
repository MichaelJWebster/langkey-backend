import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import audio, auth
from app.config import get_settings
from app.db import engine
from app.routes import hello, progress, reviews
from app.seed import seed_data


def _run_migrations() -> None:
    cfg = Config(str(Path(__file__).parent.parent / "alembic.ini"))
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Alembic's async env calls asyncio.run, so it needs its own thread.
    await asyncio.to_thread(_run_migrations)
    await seed_data()
    yield
    await engine.dispose()


app = FastAPI(title="LangKey API", version="0.2.0", lifespan=lifespan)

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


@app.get("/api/v1/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
