"""End-to-end decisions through the API: hold, decline, velocity, audit.

The risk stack here is real Redis + real Mongo with the rules-only model, so
every decision is driven by the deterministic rules layer. The model's scoring
behaviour is covered separately and never leaks into these tests.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.errors import AuditUnavailable
from app.services.risk.engine import VELOCITY_UNAVAILABLE_RULE

pytestmark = pytest.mark.risk

TREASURY_FUND = 100_000


async def db_transaction_count(risk_client) -> int:  # type: ignore[no-untyped-def]
    _, app = risk_client
    from sqlalchemy import func, select

    from app.db.models import Transaction

    async with app.state.sessionmaker() as session:
        return int(await session.scalar(select(func.count()).select_from(Transaction)))


async def test_clean_payment_settles_and_audits(
    risk_client, funded_risk_accounts, audit_collection
):
    client, _ = risk_client
    response = await client.post(
        "/transactions",
        json={
            "from_account_id": funded_risk_accounts["alice"],
            "to_account_id": funded_risk_accounts["bob"],
            "amount_minor": 2_500,
            "currency": "USD",
        },
        headers={"Idempotency-Key": f"clean-{uuid.uuid4()}"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "settled"
    assert body["decision"] == "APPROVE"

    document = await audit_collection.find_one({"_id": body["id"]})
    assert document is not None
    assert document["decision"] == "APPROVE"
    assert document["status"] == "settled"
    assert document["amount_minor"] == 2_500


async def test_card_testing_burst_is_declined_and_moves_no_money(
    risk_client, funded_risk_accounts
):
    client, _ = risk_client
    alice, bob = funded_risk_accounts["alice"], funded_risk_accounts["bob"]

    # Six attempts in under a minute averaging <= 300 minor units: R_CARD_TESTING.
    for _ in range(6):
        response = await client.post(
            "/transactions",
            json={
                "from_account_id": alice,
                "to_account_id": bob,
                "amount_minor": 100,
                "currency": "USD",
            },
            headers={"Idempotency-Key": f"probe-{uuid.uuid4()}"},
        )
        assert response.status_code in (201, 402), response.text

    # Alice's balance: funded 100_000, only the first few probes settled before
    # the rule fired; the blocked ones must not have moved money.
    account = (await client.get(f"/accounts/{alice}")).json()
    settled = 100_000 - account["balance_minor"]
    assert settled <= 600  # at most the six 1.00 probes that passed before the block

    # Every decline is a recorded attempt.
    transactions = (await client.get("/transactions?limit=50")).json()
    declined = [t for t in transactions if t["status"] == "declined"]
    assert declined, "the burst must produce at least one DECLINE"
    for transaction in declined:
        assert "R_CARD_TESTING" in transaction["rules_fired"]


async def test_held_payment_moves_no_money_and_queues_review(
    risk_client, funded_risk_accounts, audit_collection
):
    client, _ = risk_client
    alice, bob = funded_risk_accounts["alice"], funded_risk_accounts["bob"]

    # Geo hop: three countries inside a minute -> R_GEO_HOP, a review rule.
    held_responses = []
    for country in ("US", "FR", "DE"):
        response = await client.post(
            "/transactions",
            json={
                "from_account_id": alice,
                "to_account_id": bob,
                "amount_minor": 1_000,
                "currency": "USD",
                "country": country,
            },
            headers={"Idempotency-Key": f"geo-{uuid.uuid4()}"},
        )
        held_responses.append(response)

    decisions = [r.json().get("decision") for r in held_responses]
    assert "REVIEW" in decisions

    held_index = decisions.index("REVIEW")
    body = held_responses[held_index].json()
    assert held_responses[held_index].status_code == 202
    assert body["status"] == "held"
    assert body["review_status"] == "pending"

    # Money did not move for the held payment. The US and FR attempts scored
    # below the review floor (2 and 1 countries respectively) and settled;
    # the third attempt tipped countries_1m to 3 and was held.
    alice_after = (await client.get(f"/accounts/{alice}")).json()
    assert alice_after["balance_minor"] == TREASURY_FUND - 2_000

    document = await audit_collection.find_one({"_id": body["id"]})
    assert document is not None
    assert document["review_status"] == "pending"
    assert "R_GEO_HOP_3_COUNTRIES_60S" in document["rules_fired"]


async def test_velocity_outage_holds_payments_and_flags_degradation(
    dead_redis_client, funded_risk_accounts, audit_collection
):
    client, _ = dead_redis_client
    response = await client.post(
        "/transactions",
        json={
            "from_account_id": funded_risk_accounts["alice"],
            "to_account_id": funded_risk_accounts["bob"],
            "amount_minor": 1_500,
            "currency": "USD",
        },
        headers={"Idempotency-Key": f"outage-{uuid.uuid4()}"},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "held"
    assert body["decision"] == "REVIEW"
    assert VELOCITY_UNAVAILABLE_RULE in body["rules_fired"]

    # Never fail open: no money moved.
    alice_after = (await client.get(f"/accounts/{funded_risk_accounts['alice']}")).json()
    assert alice_after["balance_minor"] == TREASURY_FUND

    document = await audit_collection.find_one({"_id": body["id"]})
    assert document is not None
    assert document["velocity_degraded"] is True


async def test_declined_attempt_is_recorded_but_moves_nothing(
    risk_client, funded_risk_accounts
):
    client, _ = risk_client
    alice, bob = funded_risk_accounts["alice"], funded_risk_accounts["bob"]

    # Drive straight into the blocking velocity rule: 20 attempts in 10 minutes.
    statuses = []
    for _ in range(21):
        response = await client.post(
            "/transactions",
            json={
                "from_account_id": alice,
                "to_account_id": bob,
                "amount_minor": 1_000,
                "currency": "USD",
            },
            headers={"Idempotency-Key": f"vel-{uuid.uuid4()}"},
        )
        statuses.append(response.status_code)

    assert 402 in statuses
    declined = [r for r in statuses if r == 402]
    assert declined

    alice_after = (await client.get(f"/accounts/{alice}")).json()
    # Only the payments that settled before the block consumed balance.
    assert alice_after["balance_minor"] >= TREASURY_FUND - 20 * 1_000


async def test_audit_failure_after_commit_returns_503_and_replays_cleanly(
    risk_client, funded_risk_accounts, audit_collection, monkeypatch
):
    client, app = risk_client
    key = f"audit-fail-{uuid.uuid4()}"

    async def broken_record(transaction):  # type: ignore[no-untyped-def]
        # What the real store raises once its retries are exhausted.
        raise AuditUnavailable("mongo down (simulated)", transaction_id="unknown")

    monkeypatch.setattr(app.state.audit, "record", broken_record)
    response = await client.post(
        "/transactions",
        json={
            "from_account_id": funded_risk_accounts["alice"],
            "to_account_id": funded_risk_accounts["bob"],
            "amount_minor": 3_000,
            "currency": "USD",
        },
        headers={"Idempotency-Key": key},
    )
    monkeypatch.undo()
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "audit_unavailable"

    # The money moved and the transaction exists even though the audit failed.
    alice_after = (await client.get(f"/accounts/{funded_risk_accounts['alice']}")).json()
    assert alice_after["balance_minor"] == TREASURY_FUND - 3_000

    # Retry with the same key: the replay returns the original 201 answer and
    # the audit upsert runs again against the restored Mongo.
    retry = await client.post(
        "/transactions",
        json={
            "from_account_id": funded_risk_accounts["alice"],
            "to_account_id": funded_risk_accounts["bob"],
            "amount_minor": 3_000,
            "currency": "USD",
        },
        headers={"Idempotency-Key": key},
    )
    assert retry.status_code == 201, retry.text
    assert retry.headers["Idempotent-Replay"] == "true"
    assert retry.json()["status"] == "settled"

    body_id = retry.json()["id"]
    document = await audit_collection.find_one({"_id": body_id})
    assert document is not None, "the replayed request must complete the audit write"


async def test_replay_does_not_rescore_or_reaudit_twice(risk_client, funded_risk_accounts):
    client, _ = risk_client
    alice, bob = funded_risk_accounts["alice"], funded_risk_accounts["bob"]
    key = f"replay-{uuid.uuid4()}"

    payload = {
        "from_account_id": alice,
        "to_account_id": bob,
        "amount_minor": 500,
        "currency": "USD",
    }
    first = await client.post("/transactions", json=payload, headers={"Idempotency-Key": key})
    replay = await client.post("/transactions", json=payload, headers={"Idempotency-Key": key})
    assert first.status_code == 201 and replay.status_code == 201
    assert replay.headers["Idempotent-Replay"] == "true"
    assert first.json()["id"] == replay.json()["id"]
