"""Create tables and seed dev data on startup.

M0 shortcut: schema is applied with `create_all`. Alembic migrations arrive in
M1 once the real learning-domain schema lands (recorded in ARCHITECTURE.md).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth import hash_password
from app.db import Base, session_factory
from app.models import AppMeta, User

DEV_USER_EMAIL = "dev@langkey.dev"
DEV_USER_PASSWORD = "langkey-dev"


async def create_schema_and_seed(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        if await session.get(AppMeta, "greeting") is None:
            session.add(AppMeta(key="greeting", value="Hei! Velkommen til LangKey 🇳🇴"))
        existing = await session.scalar(select(User).where(User.email == DEV_USER_EMAIL))
        if existing is None:
            session.add(
                User(
                    email=DEV_USER_EMAIL,
                    display_name="Dev Learner",
                    password_hash=hash_password(DEV_USER_PASSWORD),
                )
            )
        await session.commit()
