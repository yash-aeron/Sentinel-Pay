"""The SSE feed: dashboard receives payment and review events live.

The stream is exercised through its generator rather than over HTTP: httpx's
``ASGITransport`` buffers a response until the app finishes, which an
endless SSE stream never does. The route adds only auth and content-type on
top of ``_event_generator``; the auth gate is tested over HTTP, the stream
logic here, and the SSE framing in ``tests/unit/test_events.py``.

Because the generator subscribes lazily (on its first pull), every test
advances it *before* triggering the publishes it wants to observe.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from app.api.routes.events import _event_generator
from tests.risk.conftest import ANALYST, hold_a_payment

pytestmark = pytest.mark.risk


async def _start(generator):  # type: ignore[no-untyped-def]
    """Advance past the retry/hello frames so the queue is subscribed."""
    async with asyncio.timeout(5):
        retry = await generator.__anext__()
        hello = await generator.__anext__()
    assert retry == "retry: 3000\n\n"
    assert hello.startswith("event: connected\n")


async def _collect_until(generator, marker: str) -> list[str]:
    """Pull SSE frames until one contains the marker; return every frame."""
    frames: list[str] = []
    async with asyncio.timeout(5):
        async for frame in generator:
            frames.append(frame)
            if marker in frame:
                break
    return frames


def _payload(frame: str) -> dict:
    for line in frame.splitlines():
        if line.startswith("data: "):
            return json.loads(line.removeprefix("data: "))
    raise AssertionError(f"no data line in frame: {frame!r}")


async def test_events_endpoint_requires_token(risk_client):
    client, _ = risk_client
    response = await client.get("/events")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_stream_sends_retry_and_hello_first(risk_client):
    _, app = risk_client
    await _start(_event_generator(app.state.events))


async def test_stream_delivers_payment_events(risk_client, funded_risk_accounts):
    client, app = risk_client
    generator = _event_generator(app.state.events)
    await _start(generator)

    response = await client.post(
        "/transactions",
        json={
            "from_account_id": funded_risk_accounts["alice"],
            "to_account_id": funded_risk_accounts["bob"],
            "amount_minor": 700,
            "currency": "USD",
        },
        headers={"Idempotency-Key": f"sse-{uuid.uuid4()}"},
    )
    assert response.status_code == 201, response.text

    frames = await _collect_until(generator, "transaction.created")
    event = _payload(frames[-1])
    assert event["type"] == "transaction.created"
    assert event["transaction"]["status"] == "settled"
    assert event["transaction"]["amount_minor"] == 700


async def test_stream_delivers_review_decision_events(risk_client, funded_risk_accounts):
    client, app = risk_client
    held = await hold_a_payment(client, funded_risk_accounts, 1_000)
    generator = _event_generator(app.state.events)
    await _start(generator)

    response = await client.post(f"/transactions/{held['id']}/reject", headers=ANALYST)
    assert response.status_code == 200, response.text

    frames = await _collect_until(generator, "review.decided")
    event = _payload(frames[-1])
    assert event["type"] == "review.decided"
    assert event["action"] == "reject"
    assert event["status"] == "declined"


async def test_replays_are_not_announced(risk_client, funded_risk_accounts):
    client, app = risk_client
    key = f"sse-replay-{uuid.uuid4()}"
    payload = {
        "from_account_id": funded_risk_accounts["alice"],
        "to_account_id": funded_risk_accounts["bob"],
        "amount_minor": 500,
        "currency": "USD",
    }
    await client.post("/transactions", json=payload, headers={"Idempotency-Key": key})

    generator = _event_generator(app.state.events)
    await _start(generator)

    # The replay returns the stored 201 with the replay marker and must not
    # announce itself a second time.
    replay = await client.post(
        "/transactions", json=payload, headers={"Idempotency-Key": key}
    )
    assert replay.status_code == 201
    assert replay.headers["Idempotent-Replay"] == "true"

    # A sentinel proves the stream is alive and that nothing from the replay
    # arrived before it.
    app.state.events.publish("transaction.created", transaction={"sentinel": True})
    frames = await _collect_until(generator, "sentinel")
    assert sum("transaction.created" in frame for frame in frames) == 1
    assert _payload(frames[-1])["transaction"] == {"sentinel": True}
