from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import SessionMakerDep

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", responses={503: {}})
async def ready(sessionmaker: SessionMakerDep) -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        async with sessionmaker() as session:
            await session.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001 - readiness must report, not raise
        checks["postgres"] = f"error: {type(exc).__name__}"

    healthy = all(value == "ok" for value in checks.values())
    return JSONResponse(
        content={"status": "ok" if healthy else "degraded", "checks": checks},
        status_code=200 if healthy else 503,
    )
