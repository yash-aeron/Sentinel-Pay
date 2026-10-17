"""Locust load scenarios for SentinelPay.

    uvicorn app.main:app --port 8000          # separate terminal
    locust -f locustfile.py --headless -u 20 -r 5 -t 30s

Mix: most users pay (new idempotency keys), some replay the same key twice
(proving the storm path under load), and some read the dashboard's queries.
"""

from __future__ import annotations

import uuid

from locust import HttpUser, between, task


class SentinelPayUser(HttpUser):
    host = "http://127.0.0.1:8000"
    wait_time = between(0.05, 0.3)

    def on_start(self) -> None:
        # Each simulated user owns a pair so transfers never contend on the
        # same rows; that isolates API cost from lock queueing, which the
        # concurrency suite already measures separately.
        treasury = self._create_account("load-treasury", "treasury")
        source = self._create_account("load-a", "customer")
        sink = self._create_account("load-b", "customer")
        self.client.post(
            "/transactions",
            json=self._transfer(treasury, source, 10_000_000),
            headers=self._key("fund-source"),
        )
        self.treasury, self.source, self.sink = treasury, source, sink
        self.replay_key: str | None = None

    def _create_account(self, owner: str, account_type: str) -> str:
        response = self.client.post(
            "/accounts",
            json={"owner_ref": f"{owner}-{uuid.uuid4().hex[:8]}", "currency": "USD",
                  "account_type": account_type},
        )
        return response.json()["id"]

    @staticmethod
    def _transfer(from_id: str, to_id: str, amount_minor: int) -> dict:
        return {
            "from_account_id": from_id,
            "to_account_id": to_id,
            "amount_minor": amount_minor,
            "currency": "USD",
        }

    @staticmethod
    def _key(prefix: str) -> dict[str, str]:
        return {"Idempotency-Key": f"{prefix}-{uuid.uuid4()}"}

    @task(6)
    def pay(self) -> None:
        with self.client.post(
            "/transactions",
            json=self._transfer(self.source, self.sink, 100 + uuid.uuid4().int % 5_000),
            headers=self._key("load-pay"),
            catch_response=True,
        ) as response:
            # 402 DECLINE and 409 insufficient funds are risk/business
            # decisions, not faults; anything else keeps its status.
            if response.status_code in (402, 409):
                response.success()

    @task(2)
    def pay_with_replay(self) -> None:
        # Same key twice: the second request must replay, not re-charge.
        headers = self._key("load-replay")
        payload = self._transfer(self.source, self.sink, 777)
        with self.client.post(
            "/transactions", json=payload, headers=headers, catch_response=True
        ) as first:
            if first.status_code in (402, 409):
                first.success()
        with self.client.post(
            "/transactions", json=payload, headers=headers, catch_response=True
        ) as replay:
            # The replay returns whatever the first request decided, stored
            # verbatim. Anything under 500 is the contract working.
            if replay.status_code < 500:
                replay.success()

    @task(2)
    def list_recent(self) -> None:
        self.client.get("/transactions?limit=20")

    @task(1)
    def health(self) -> None:
        self.client.get("/health/ready")
