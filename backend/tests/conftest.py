from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

import app.db.models  # noqa: F401 - registers tables on Base.metadata
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import create_engine, create_sessionmaker
from app.main import create_app
from app.services.wiring import build_services, close_services

TABLES = "ledger_entries, idempotency_keys, transactions, accounts"


def _dsn(settings: Settings, database: str) -> str:
    return (
        f"postgresql://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{database}"
    )


async def _recreate_database(settings: Settings) -> None:
    conn = await asyncpg.connect(_dsn(settings, "postgres"))
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{settings.test_postgres_db}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{settings.test_postgres_db}"')
    finally:
        await conn.close()

    engine = create_engine(settings.test_database_url, pool_size=2)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def _truncate(database_url: str) -> None:
    engine = create_engine(database_url, pool_size=1)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    await engine.dispose()


@pytest.fixture(scope="session")
def database_url() -> str:
    settings = get_settings()
    asyncio.run(_recreate_database(settings))
    return settings.test_database_url


@pytest.fixture(autouse=True)
def _clean_state(database_url: str) -> None:
    asyncio.run(_truncate(database_url))


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    # Large pool: the duplicate-storm test needs ~50 requests genuinely in
    # flight at once, otherwise it would be measuring pool queueing.
    eng = create_engine(database_url, pool_size=60, max_overflow=10)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)
    # ASGITransport never runs the lifespan, so the app.state attributes the
    # routes depend on must be injected here. Risk is off: these tests exercise
    # money movement, and must not depend on Redis or Mongo being reachable.
    services = await build_services(
        get_settings(), app.state.sessionmaker, risk_enabled=False
    )
    app.state.services = services
    app.state.payments = services.payments
    app.state.review = services.review
    app.state.events = services.events
    app.state.velocity = services.velocity
    app.state.audit = services.audit
    app.state.risk_model = services.model
    app.state.copilot = services.copilot
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://sentinelpay.test"
        ) as http:
            yield http
    finally:
        await close_services(services)


async def create_account(
    client: AsyncClient, owner_ref: str, account_type: str = "customer"
) -> dict:
    response = await client.post(
        "/accounts",
        json={"owner_ref": owner_ref, "currency": "USD", "account_type": account_type},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def fund(client: AsyncClient, treasury_id: str, account_id: str, amount: int) -> None:
    response = await client.post(
        "/transactions",
        json={
            "from_account_id": treasury_id,
            "to_account_id": account_id,
            "amount_minor": amount,
            "currency": "USD",
            "description": "opening balance",
        },
        headers={"Idempotency-Key": f"seed-{uuid.uuid4()}"},
    )
    assert response.status_code == 201, response.text


@pytest_asyncio.fixture
async def treasury(client: AsyncClient) -> dict:
    return await create_account(client, "system-treasury", account_type="treasury")


@pytest_asyncio.fixture
async def funded_pair(client: AsyncClient, treasury: dict) -> dict[str, str]:
    """Two customer accounts, 1,000.00 USD each, funded from treasury."""
    alice = await create_account(client, "alice")
    bob = await create_account(client, "bob")
    await fund(client, treasury["id"], alice["id"], 100_000)
    await fund(client, treasury["id"], bob["id"], 100_000)
    return {"alice": alice["id"], "bob": bob["id"], "treasury": treasury["id"]}
