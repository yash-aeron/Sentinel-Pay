from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.api.routes import accounts, copilot, events, health, review, transactions
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger
from app.core.observability import (
    RATE_LIMITED_ROUTES,
    Metrics,
    MetricsMiddleware,
    RateLimitMiddleware,
)
from app.db.session import create_engine, create_sessionmaker
from app.services.wiring import build_services, close_services

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    # Tests inject engine/sessionmaker/services into app.state before any
    # request; only build our own when the app is actually serving.
    owns_engine = getattr(app.state, "engine", None) is None
    if owns_engine:
        engine = create_engine(settings.database_url)
        app.state.engine = engine
        app.state.sessionmaker = create_sessionmaker(engine)
        log.info("engine_ready", env=settings.app_env)

    if getattr(app.state, "payments", None) is None:
        services = await build_services(settings, app.state.sessionmaker)
        app.state.services = services
        app.state.payments = services.payments
        app.state.review = services.review
        app.state.events = services.events
        app.state.velocity = services.velocity
        app.state.audit = services.audit
        app.state.risk_model = services.model
        app.state.copilot = services.copilot

    try:
        yield
    finally:
        existing = getattr(app.state, "services", None)
        if existing is not None:
            await close_services(existing)
        if owns_engine:
            await app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="SentinelPay",
        version="0.2.0",
        description="Idempotent payment engine with real-time fraud risk scoring.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.metrics = Metrics()

    @app.exception_handler(AppError)
    async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {"code": exc.code, "message": exc.message, "context": exc.context}
            },
        )

    app.add_middleware(MetricsMiddleware, metrics=app.state.metrics)
    app.add_middleware(
        RateLimitMiddleware,
        limits={
            route: getattr(settings, settings_key)
            for route, settings_key in RATE_LIMITED_ROUTES.items()
        },
    )

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        # Only the raw counters; a real deployment puts a scrape endpoint
        # behind the network edge, not on the public API.
        if request.app.state.settings.metrics_protected:
            token = request.headers.get("X-Analyst-Token")
            if token != request.app.state.settings.analyst_token:
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": {
                            "code": "unauthorized",
                            "message": "analyst token required",
                            "context": {},
                        }
                    },
                )
        return Response(
            content=request.app.state.metrics.render(), media_type="text/plain"
        )

    app.include_router(health.router)
    app.include_router(accounts.router)
    app.include_router(transactions.router)
    app.include_router(review.router)
    app.include_router(events.router)
    app.include_router(copilot.router)
    return app


app = create_app()
