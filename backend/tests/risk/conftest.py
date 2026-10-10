"""Fixtures for risk integration tests: real Redis (db 15) and real MongoDB.

These tests intentionally do not use the default ``client`` fixture, which
builds the app with risk disabled. Here the full stack is exercised: velocity
windows, the rules layer, and the Mongo audit write.

The model artifact is pointed at a path that never exists, forcing
``RulesOnlyModel``. Tests must stay deterministic regardless of whether
``python -m ml.train`` has been run; the model's own behaviour is covered by
the unit and trainer round-trip tests.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
import redis.asyncio as aioredis
from httpx import ASGITransport, AsyncClient, Response
from pymongo import AsyncMongoClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings, get_settings
from app.db.session import create_sessionmaker
from app.main import create_app
from app.services.audit import COLLECTION
from app.services.wiring import build_services, close_services

# A port with no listener on every platform: connection refused, immediately.
DEAD_REDIS_URL = "redis://127.0.0.1:1/15"


def _risk_settings(**overrides: object) -> Settings:
    settings = get_settings()
    updates: dict[str, object] = {
        "risk_enabled": True,
        "redis_url": settings.redis_test_url,
        "mongo_db": settings.mongo_test_db,
        # Deterministic analyst token for the review surface.
        "analyst_token": "test-analyst-token",
        # Never accidentally load the real artifact from backend/ml/artifacts.
        "risk_model_path": str(Path(__file__).parent / "does-not-exist.joblib"),
        "risk_model_meta_path": str(Path(__file__).parent / "does-not-exist.json"),
    }
    return settings.model_copy(update=updates | overrides)


@pytest_asyncio.fixture
async def velocity_redis() -> AsyncIterator[aioredis.Redis]:
    """Redis on the dedicated test database, flushed before and after."""
    client = aioredis.Redis.from_url(get_settings().redis_test_url)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest_asyncio.fixture
async def audit_collection() -> AsyncIterator:
    """The Mongo audit collection, emptied before and after each test."""
    settings = get_settings()
    mongo = AsyncMongoClient(settings.mongo_url)
    database = mongo[settings.mongo_test_db]
    await database.drop_collection(COLLECTION)
    yield database[COLLECTION]
    await database.drop_collection(COLLECTION)
    await mongo.close()


def _inject(app, engine: AsyncEngine, settings: Settings):  # type: ignore[no-untyped-def]
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)


async def _wire(app, settings: Settings):  # type: ignore[no-untyped-def]
    services = await build_services(
        settings, app.state.sessionmaker, copilot_enabled=False
    )
    app.state.services = services
    app.state.payments = services.payments
    app.state.review = services.review
    app.state.events = services.events
    app.state.velocity = services.velocity
    app.state.audit = services.audit
    app.state.risk_model = services.model
    app.state.copilot = services.copilot
    return services


@pytest_asyncio.fixture
async def risk_client(
    engine: AsyncEngine, velocity_redis, audit_collection
) -> AsyncIterator[tuple[AsyncClient, object]]:
    """Full-stack app: real Redis velocity, real Mongo audit, rules-only model."""
    settings = _risk_settings()
    app = create_app(settings)
    _inject(app, engine, settings)
    services = await _wire(app, settings)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://sentinelpay.test"
        ) as http:
            yield http, app
    finally:
        await close_services(services)


@pytest_asyncio.fixture
async def dead_redis_client(
    engine: AsyncEngine, audit_collection
) -> AsyncIterator[tuple[AsyncClient, object]]:
    """Risk on, Redis unreachable: every payment must hold for review."""
    settings = _risk_settings(redis_url=DEAD_REDIS_URL)
    app = create_app(settings)
    _inject(app, engine, settings)
    services = await _wire(app, settings)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://sentinelpay.test"
        ) as http:
            yield http, app
    finally:
        await close_services(services)


async def open_account(client: AsyncClient, owner_ref: str, *, treasury: bool = False) -> dict:
    response = await client.post(
        "/accounts",
        json={
            "owner_ref": owner_ref,
            "currency": "USD",
            "account_type": "treasury" if treasury else "customer",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def pay(
    client: AsyncClient,
    from_id: str,
    to_id: str,
    amount_minor: int,
    *,
    country: str = "US",
    merchant_id: str | None = None,
    key: str | None = None,
) -> Response:
    return await client.post(
        "/transactions",
        json={
            "from_account_id": from_id,
            "to_account_id": to_id,
            "amount_minor": amount_minor,
            "currency": "USD",
            "country": country,
            "merchant_id": merchant_id,
        },
        headers={"Idempotency-Key": key or f"test-{uuid.uuid4()}"},
    )


ANALYST = {"X-Analyst-Token": "test-analyst-token"}


async def hold_a_payment(
    client: AsyncClient, accounts: dict[str, str], amount_minor: int
) -> dict:
    """Drive a payment into the review queue via the geo-hop rule.

    Two small payments in the US and France settle first (2_000 minor units
    total leaves alice's balance); the third attempt, in a third country,
    trips ``countries_1m >= 3`` and is held at the requested amount. Keeping
    the settled prefix tiny lets tests request large held payments without
    tripping insufficient funds on the way in.
    """
    response: Response | None = None
    for country in ("US", "FR"):
        response = await pay(client, accounts["alice"], accounts["bob"], 1_000, country=country)
        assert response.status_code == 201, response.text
    response = await pay(client, accounts["alice"], accounts["bob"], amount_minor, country="DE")
    assert response.status_code == 202, response.text
    return response.json()


@pytest_asyncio.fixture
async def funded_risk_accounts(risk_client) -> dict[str, str]:
    """Treasury plus alice and bob, each funded 1_000.00 through the ledger."""
    client, _ = risk_client
    treasury = await open_account(client, "system-treasury", treasury=True)
    alice = await open_account(client, "alice")
    bob = await open_account(client, "bob")
    for account in (alice, bob):
        response = await pay(client, treasury["id"], account["id"], 100_000)
        assert response.status_code == 201, response.text
    return {"treasury": treasury["id"], "alice": alice["id"], "bob": bob["id"]}
