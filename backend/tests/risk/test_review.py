"""The analyst review surface: approve settles real money, reject declines.

Held payments are produced the honest way here — through the geo-hop review
rule with the real velocity stack — so the tests exercise exactly the state
the dashboard will meet.
"""

from __future__ import annotations

import uuid

import pytest

from tests.risk.conftest import ANALYST, hold_a_payment

pytestmark = pytest.mark.risk

TREASURY_FUND = 100_000


async def test_approve_without_token_is_unauthorized(risk_client, funded_risk_accounts):
    client, _ = risk_client
    held = await hold_a_payment(client, funded_risk_accounts, 1_000)
    response = await client.post(f"/transactions/{held['id']}/approve")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"

    with_wrong = await client.post(
        f"/transactions/{held['id']}/approve",
        headers={"X-Analyst-Token": "wrong"},
    )
    assert with_wrong.status_code == 401


async def test_approve_settles_and_posts_ledger_entries(risk_client, funded_risk_accounts):
    client, _ = risk_client
    held = await hold_a_payment(client, funded_risk_accounts, 2_000)
    alice_before = (await client.get(f"/accounts/{funded_risk_accounts['alice']}")).json()

    response = await client.post(
        f"/transactions/{held['id']}/approve", headers=ANALYST
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "settled"
    assert body["review_status"] == "approved"
    assert body["decision"] == "REVIEW"  # forensic record keeps the risk call

    alice_after = (await client.get(f"/accounts/{funded_risk_accounts['alice']}")).json()
    assert alice_after["balance_minor"] == alice_before["balance_minor"] - 2_000

    detail = (await client.get(f"/transactions/{held['id']}")).json()
    assert {entry["direction"] for entry in detail["entries"]} == {"debit", "credit"}


async def test_approve_is_idempotent(risk_client, funded_risk_accounts):
    client, _ = risk_client
    held = await hold_a_payment(client, funded_risk_accounts, 1_500)
    first = await client.post(f"/transactions/{held['id']}/approve", headers=ANALYST)
    second = await client.post(f"/transactions/{held['id']}/approve", headers=ANALYST)
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"] == held["id"]

    # Exactly one settlement: the balance moved only the held amount.
    alice = (await client.get(f"/accounts/{funded_risk_accounts['alice']}")).json()
    assert alice["balance_minor"] == TREASURY_FUND - 2_000 - 1_500  # two settled, one held


async def test_reject_declines_and_never_moves_money(risk_client, funded_risk_accounts):
    client, _ = risk_client
    held = await hold_a_payment(client, funded_risk_accounts, 1_000)
    alice_before = (await client.get(f"/accounts/{funded_risk_accounts['alice']}")).json()

    response = await client.post(f"/transactions/{held['id']}/reject", headers=ANALYST)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "declined"
    assert body["review_status"] == "rejected"

    alice_after = (await client.get(f"/accounts/{funded_risk_accounts['alice']}")).json()
    assert alice_after["balance_minor"] == alice_before["balance_minor"]

    detail = (await client.get(f"/transactions/{held['id']}")).json()
    assert detail["entries"] == []  # no ledger legs for a rejection


async def test_approving_a_rejected_payment_changes_nothing(risk_client, funded_risk_accounts):
    client, _ = risk_client
    held = await hold_a_payment(client, funded_risk_accounts, 1_000)
    await client.post(f"/transactions/{held['id']}/reject", headers=ANALYST)
    again = await client.post(f"/transactions/{held['id']}/approve", headers=ANALYST)
    assert again.status_code == 200
    assert again.json()["status"] == "declined"
    assert again.json()["review_status"] == "rejected"


async def test_approve_fails_when_funds_are_gone_and_payment_stays_held(
    risk_client, funded_risk_accounts
):
    client, _ = risk_client
    # A held payment never took the funds check (money only moves on APPROVE),
    # so requesting more than alice can ever have is exactly the situation the
    # approval-time re-check exists for.
    held = await hold_a_payment(client, funded_risk_accounts, 200_000)

    response = await client.post(f"/transactions/{held['id']}/approve", headers=ANALYST)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "insufficient_funds"

    still = (await client.get(f"/transactions/{held['id']}")).json()
    assert still["status"] == "held"
    assert still["review_status"] == "pending"
    assert still["entries"] == []

    # The analyst can now reject it instead.
    rejected = await client.post(f"/transactions/{held['id']}/reject", headers=ANALYST)
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "declined"


async def test_review_of_unknown_transaction_is_404(risk_client):
    client, _ = risk_client
    response = await client.post(
        f"/transactions/{uuid.uuid4()}/approve", headers=ANALYST
    )
    assert response.status_code == 404


async def test_queue_filter_lists_only_held(risk_client, funded_risk_accounts):
    client, _ = risk_client
    held = await hold_a_payment(client, funded_risk_accounts, 1_000)
    queue = (await client.get("/transactions?status=held")).json()
    assert [row["id"] for row in queue] == [held["id"]]


async def test_concurrent_approves_settle_exactly_once(risk_client, funded_risk_accounts):
    import asyncio

    client, _ = risk_client
    held = await hold_a_payment(client, funded_risk_accounts, 3_000)

    responses = await asyncio.gather(
        client.post(f"/transactions/{held['id']}/approve", headers=ANALYST),
        client.post(f"/transactions/{held['id']}/approve", headers=ANALYST),
    )
    assert all(r.status_code == 200 for r in responses)
    # Both report the same settled reality, and the balance moved once.
    assert {r.json()["status"] for r in responses} == {"settled"}
    alice = (await client.get(f"/accounts/{funded_risk_accounts['alice']}")).json()
    assert alice["balance_minor"] == TREASURY_FUND - 2_000 - 3_000
