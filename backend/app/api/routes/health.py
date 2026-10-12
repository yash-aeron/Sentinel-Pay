"""Health probes.

``live`` answers from the process alone. ``ready`` checks every datastore the
API depends on, but only the ones it was actually built with: a risk-off
deployment has no Redis or Mongo and must not report their absence as
unhealthy.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import SessionMakerDep

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", responses={503: {}})
async def ready(sessionmaker: SessionMakerDep, request: Request) -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        async with sessionmaker() as session:
            await session.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001 - readiness must report, not raise
        checks["postgres"] = f"error: {type(exc).__name__}"

    velocity = request.app.state.velocity
    if velocity is not None:
        try:
            await velocity.ping()
            checks["redis"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["redis"] = f"error: {type(exc).__name__}"

    audit = request.app.state.audit
    if audit is not None:
        try:
            await audit.ping()
            checks["mongo"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["mongo"] = f"error: {type(exc).__name__}"

    healthy = all(value == "ok" for value in checks.values())
    return JSONResponse(
        content={"status": "ok" if healthy else "degraded", "checks": checks},
        status_code=200 if healthy else 503,
    )
