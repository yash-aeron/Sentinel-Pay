"""Rate limiting, metrics, and the readiness probe.

The rate-limit tests pin a tiny budget through settings so they are fast and
hermetic; the metrics and health tests run against the default app.
"""

from __future__ import annotations

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings
from app.db.session import create_sessionmaker
from app.main import create_app
from app.services.wiring import build_services, close_services

ANALYST_TOKEN = "test-analyst-token"  # noqa: S105 - deterministic fixture


@pytest_asyncio.fixture
async def limited_client(engine: AsyncEngine):
    """Risk-off app whose payment budget is 3 per minute."""
    settings = get_settings().model_copy(
        update={"rate_limit_payments_per_minute": 3, "analyst_token": ANALYST_TOKEN}
    )
    app = create_app(settings)
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)
    services = await build_services(settings, app.state.sessionmaker, risk_enabled=False)
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


async def _open_two_accounts(client: AsyncClient) -> tuple[str, str]:
    """A treasury source (never overdraft-checked) and a customer sink."""
    source = (
        await client.post(
            "/accounts",
            json={"owner_ref": "rl-treasury", "currency": "USD", "account_type": "treasury"},
        )
    ).json()
    sink = (await client.post("/accounts", json={"owner_ref": "rl-b", "currency": "USD"})).json()
    return source["id"], sink["id"]


async def test_payment_rate_limit_returns_429(limited_client):
    from_id, to_id = await _open_two_accounts(limited_client)
    statuses = []
    for _ in range(5):
        response = await limited_client.post(
            "/transactions",
            json={
                "from_account_id": from_id,
                "to_account_id": to_id,
                "amount_minor": 100,
                "currency": "USD",
            },
            headers={"Idempotency-Key": f"rl-{uuid.uuid4()}"},
        )
        statuses.append(response.status_code)
    assert statuses[:3] == [201, 201, 201]
    assert statuses[3:] == [429, 429]

    limited = await limited_client.post(
        "/transactions",
        json={
            "from_account_id": from_id,
            "to_account_id": to_id,
            "amount_minor": 100,
            "currency": "USD",
        },
        headers={"Idempotency-Key": f"rl-{uuid.uuid4()}"},
    )
    body = limited.json()["error"]
    assert body["code"] == "rate_limited"
    assert "retry_after_seconds" in body["context"]
    assert limited.headers["Retry-After"]


async def test_rate_limit_budget_is_per_client(limited_client):
    from_id, to_id = await _open_two_accounts(limited_client)
    payload = {
        "from_account_id": from_id,
        "to_account_id": to_id,
        "amount_minor": 100,
        "currency": "USD",
    }
    for _ in range(3):
        response = await limited_client.post(
            "/transactions",
            json=payload,
            headers={"Idempotency-Key": f"rl-a-{uuid.uuid4()}", "X-API-Key": "client-a"},
        )
        assert response.status_code == 201

    # A different client key has its own bucket even though the path is same.
    other = await limited_client.post(
        "/transactions",
        json=payload,
        headers={"Idempotency-Key": f"rl-b-{uuid.uuid4()}", "X-API-Key": "client-b"},
    )
    assert other.status_code == 201


async def test_reads_are_never_rate_limited(limited_client):
    for _ in range(10):
        response = await limited_client.get("/transactions")
        assert response.status_code == 200


async def test_metrics_endpoint_counts_and_requires_token(limited_client):
    await limited_client.get("/transactions")
    metrics = await limited_client.get("/metrics")
    assert metrics.status_code == 401

    ok = await limited_client.get("/metrics", headers={"X-Analyst-Token": ANALYST_TOKEN})
    assert ok.status_code == 200
    body = ok.text
    assert "sentinelpay_requests_total" in body
    assert 'route="/transactions"' in body
    assert "sentinelpay_uptime_seconds" in body


async def test_metrics_do_not_leak_path_parameters(limited_client):
    # A 404 on a parameterised path must not create one label per path.
    await limited_client.get("/transactions/not-a-uuid")
    await limited_client.get("/transactions/also-not-a-uuid")
    metrics = await limited_client.get(
        "/metrics", headers={"X-Analyst-Token": ANALYST_TOKEN}
    )
    assert "not-a-uuid" not in metrics.text
