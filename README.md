# LangKey Backend

Python/FastAPI backend for LangKey, a language-learning app. Norwegian (Bokmål) first, language-agnostic by design.

## Quick start

```bash
docker compose up --build
```

Brings up Postgres, MinIO (S3-compatible object storage), and the API at http://localhost:8000. Interactive API docs at http://localhost:8000/docs. MinIO console at http://localhost:9001 (langkey / langkey-dev).

Dev login (seeded automatically): `dev@langkey.dev` / `langkey-dev`.

## Local development without Docker (API only)

```bash
docker compose up postgres minio -d
uv sync
uv run uvicorn app.main:app --reload
```

## Tests

```bash
uv run pytest
```

Tests run against in-memory SQLite; no services required.

## Layout

- `app/main.py` — FastAPI app, CORS, startup migrations/seed
- `app/auth.py` — JWT auth (dev-grade user store, production-shaped interfaces)
- `app/db/` — database plumbing: engine, sessions, declarative `Base`
- `app/models/` — SQLAlchemy models by functional area (`users`, `meta`, `content`, `learning`), re-exported from the package root
- `app/scheduling.py` — FSRS bridge (py-fsrs ↔ `UserCard` rows)
- `app/config.py` — settings via `LANGKEY_*` env vars / `.env`
- `app/routes/` — API routes (versioned under `/api/v1`)
- `data/seed_deck_nb.json` — Norwegian A1–A2 seed deck

See `ARCHITECTURE.md` and `ROADMAP.md` in the parent workspace for decisions and plan.
