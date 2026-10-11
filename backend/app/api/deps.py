from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import Unauthorized
from app.services.events import EventBus
from app.services.payments import PaymentService
from app.services.review import ReviewService


async def get_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    """Resolved from app.state so tests can inject their own engine."""
    return request.app.state.sessionmaker


def get_payments(request: Request) -> PaymentService:
    """The configured payment service, built once during application startup."""
    return request.app.state.payments


def get_events(request: Request) -> EventBus:
    return request.app.state.events


def get_review(request: Request) -> ReviewService:
    return request.app.state.review


def get_scope(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> str:
    """Idempotency scope.

    Real auth lands in Phase 5; until then every anonymous caller shares the
    ``default`` scope, which is honest about the limitation rather than
    pretending isolation exists.
    """
    return x_api_key or "default"


def get_analyst(
    request: Request,
    x_analyst_token: Annotated[str | None, Header(alias="X-Analyst-Token")] = None,
    analyst_token: Annotated[str | None, Query(alias="analyst_token")] = None,
) -> str:
    """Analyst identification for the review surface.

    Accepts the token as a header or, only because ``EventSource`` cannot set
    headers, as an ``?analyst_token=`` query parameter for the SSE stream.
    A static shared token is a Phase 3 stand-in for real accounts; the token
    comes from the app's settings so tests can pin a deterministic value and
    nothing is hardcoded.
    """
    expected = request.app.state.settings.analyst_token
    supplied = x_analyst_token or analyst_token
    if not supplied or supplied != expected:
        raise Unauthorized("a valid X-Analyst-Token header is required")
    return supplied


SessionMakerDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]
ScopeDep = Annotated[str, Depends(get_scope)]
PaymentsDep = Annotated[PaymentService, Depends(get_payments)]
EventsDep = Annotated[EventBus, Depends(get_events)]
ReviewDep = Annotated[ReviewService, Depends(get_review)]
AnalystDep = Annotated[str, Depends(get_analyst)]
