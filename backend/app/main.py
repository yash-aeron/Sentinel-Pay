from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes import accounts, health, transactions
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger
from app.db.session import create_engine, create_sessionmaker

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    # Tests inject engine/sessionmaker into app.state before any request; only
    # build our own when the app is actually serving.
    if getattr(app.state, "engine", None) is None:
        engine = create_engine(settings.database_url)
        app.state.engine = engine
        app.state.sessionmaker = create_sessionmaker(engine)
        log.info("engine_ready", env=settings.app_env)
    yield
    await app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="SentinelPay",
        version="0.1.0",
        description="Idempotent payment engine with real-time fraud risk scoring.",
        lifespan=lifespan,
    )
    app.state.settings = settings

    @app.exception_handler(AppError)
    async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {"code": exc.code, "message": exc.message, "context": exc.context}
            },
        )

    app.include_router(health.router)
    app.include_router(accounts.router)
    app.include_router(transactions.router)
    return app


app = create_app()
