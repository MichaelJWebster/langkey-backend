from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_session
from app.models import AppMeta, User

router = APIRouter(prefix="/api/v1", tags=["hello"])


class HelloResponse(BaseModel):
    greeting: str
    display_name: str


@router.get("/hello", response_model=HelloResponse)
async def hello(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HelloResponse:
    meta = await session.get(AppMeta, "greeting")
    greeting = meta.value if meta else "Hei!"
    return HelloResponse(greeting=greeting, display_name=user.display_name)
