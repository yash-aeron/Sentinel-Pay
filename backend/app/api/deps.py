from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def get_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    """Resolved from app.state so tests can inject their own engine."""
    return request.app.state.sessionmaker


def get_scope(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> str:
    """Idempotency scope.

    Phase 1 uses the caller's API key when present. Real auth lands in Phase 5;
    until then every anonymous caller shares the ``default`` scope, which is
    honest about the limitation rather than pretending isolation exists.
    """
    return x_api_key or "default"


SessionMakerDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]
ScopeDep = Annotated[str, Depends(get_scope)]
