from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import auth
from app.config import get_settings
from app.db import engine
from app.routes import hello
from app.seed import create_schema_and_seed


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_schema_and_seed(engine)
    yield
    await engine.dispose()


app = FastAPI(title="LangKey API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(hello.router)


@app.get("/api/v1/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
