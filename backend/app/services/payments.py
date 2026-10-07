"""Payment orchestration: idempotency, risk decision, ledger, audit.

Ordering is the whole design:

1. Claim the idempotency key (advisory lock) so duplicates serialise.
2. Read the source account without locking and score the attempt. Scoring can
   involve Redis and a model; doing it before the row locks keeps lock hold
   time to the part that actually moves money.
3. Lock both accounts in id order and settle, or write a held/declined row.
4. Commit the money and the stored response in one transaction.
5. Upsert the audit document. If that fails the payment still happened; the
   caller gets 503 and a retry replays the stored response and retries the
   audit.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import (
    AccountNotFound,
    ConcurrentIdempotentRequest,
    CurrencyMismatch,
    IdempotencyKeyReuse,
    InsufficientFunds,
    SameAccountTransfer,
)
from app.core.time import utcnow
from app.db.models import Account, IdempotencyKey, Transaction
from app.schemas.transaction import TransactionResponse
from app.services import idempotency, ledger
from app.services.audit import AuditStore
from app.services.events import EventBus
from app.services.risk.decision import RiskAssessment
from app.services.risk.engine import AssessmentRequest, RiskEngine

_STATUS_BY_DECISION = {"APPROVE": "settled", "REVIEW": "held", "DECLINE": "declined"}
_STATUS_CODE_BY_DECISION = {"APPROVE": 201, "REVIEW": 202, "DECLINE": 402}


@dataclass(frozen=True, slots=True)
class PaymentCommand:
    scope: str
    idempotency_key: str
    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount_minor: int
    currency: str
    description: str | None = None
    country: str = "US"
    merchant_id: str | None = None
    device_id: str | None = None

    def canonical_payload(self) -> dict[str, Any]:
        # Every field that can change the decision is part of the hash. Reusing
        # a key with a different country is a different payment, not a retry.
        return {
            "from_account_id": str(self.from_account_id),
            "to_account_id": str(self.to_account_id),
            "amount_minor": self.amount_minor,
            "currency": self.currency,
            "description": self.description,
            "country": self.country,
            "merchant_id": self.merchant_id,
            "device_id": self.device_id,
        }


@dataclass(frozen=True, slots=True)
class PaymentOutcome:
    """``body_json`` is stored verbatim so a replay is byte-identical."""

    body_json: str
    status_code: int
    replayed: bool
    transaction_id: uuid.UUID | None


class PaymentService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        risk: RiskEngine | None = None,
        audit: AuditStore | None = None,
        events: EventBus | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._risk = risk
        self._audit = audit
        self._events = events

    async def execute(self, command: PaymentCommand) -> PaymentOutcome:
        request_hash = idempotency.canonical_hash(command.canonical_payload())
        audited: Transaction | None = None
        fresh = False
        outcome: PaymentOutcome

        async with self._sessionmaker() as session, session.begin():
            await idempotency.acquire_key_lock(
                session, scope=command.scope, key=command.idempotency_key
            )

            claim = await idempotency.find_claim(
                session, scope=command.scope, key=command.idempotency_key
            )
            if claim is not None:
                outcome = self._resolve_existing(claim, request_hash)
                if claim.transaction_id is not None:
                    audited = await session.get(Transaction, claim.transaction_id)
            else:
                claim = idempotency.new_claim(
                    scope=command.scope,
                    key=command.idempotency_key,
                    request_hash=request_hash,
                )
                session.add(claim)
                await session.flush()

                outcome, audited = await self._perform(session, command)
                fresh = True

                claim.status = "completed"
                claim.response_status = outcome.status_code
                claim.response_body = outcome.body_json
                claim.transaction_id = outcome.transaction_id

        # Postgres has committed. The audit write is deliberately outside the
        # money transaction; see the module docstring.
        if self._audit is not None and audited is not None:
            await self._audit.record(audited)

        # Only a genuinely new payment is announced; a replay is history, and
        # re-announcing it would show the same payment twice in the feed.
        if self._events is not None and fresh and audited is not None:
            self._events.publish(
                "transaction.created",
                transaction=json.loads(outcome.body_json),
            )

        return outcome

    @staticmethod
    def _resolve_existing(claim: IdempotencyKey, request_hash: str) -> PaymentOutcome:
        if claim.request_hash != request_hash:
            raise IdempotencyKeyReuse(
                "this Idempotency-Key was already used with a different request body",
                key=claim.key,
            )
        if claim.status != "completed" or claim.response_body is None:
            raise ConcurrentIdempotentRequest(
                "a request with this Idempotency-Key is still in flight", key=claim.key
            )
        return PaymentOutcome(
            body_json=claim.response_body,
            status_code=claim.response_status or 201,
            replayed=True,
            transaction_id=claim.transaction_id,
        )

    async def _perform(
        self, session: AsyncSession, command: PaymentCommand
    ) -> tuple[PaymentOutcome, Transaction]:
        if command.from_account_id == command.to_account_id:
            raise SameAccountTransfer("source and destination accounts must differ")

        # Unlocked read: only used for scoring inputs (owner, home country).
        source_preview = await session.get(Account, command.from_account_id)
        if source_preview is None:
            raise AccountNotFound(
                f"account {command.from_account_id} not found",
                account_id=str(command.from_account_id),
            )
        destination_preview = await session.get(Account, command.to_account_id)
        if destination_preview is None:
            raise AccountNotFound(
                f"account {command.to_account_id} not found",
                account_id=str(command.to_account_id),
            )
        self._require_same_currency(command, source_preview, destination_preview)

        # Generated before scoring so the velocity member carries the id of the
        # row that will eventually exist.
        transaction_id = uuid.uuid4()
        occurred_at = utcnow()
        assessment = await self._assess(
            command, source_preview, transaction_id, occurred_at
        )

        if assessment is None:
            decision = "APPROVE"
            status = "settled"
            status_code = 201
        else:
            decision = assessment.decision
            status = _STATUS_BY_DECISION[decision]
            status_code = _STATUS_CODE_BY_DECISION[decision]

        if decision == "APPROVE":
            accounts = await _lock_accounts_in_order(
                session, [command.from_account_id, command.to_account_id]
            )
            source = accounts[command.from_account_id]
            destination = accounts[command.to_account_id]

            # Checked while holding the lock. Checking earlier would be a TOCTOU
            # bug: the balance can change between scoring and settlement.
            if (
                source.account_type != "treasury"
                and source.balance_minor < command.amount_minor
            ):
                raise InsufficientFunds(
                    "source account has insufficient funds",
                    balance_minor=source.balance_minor,
                    requested_minor=command.amount_minor,
                )

            source.balance_minor -= command.amount_minor
            destination.balance_minor += command.amount_minor

        transaction = _new_transaction(
            transaction_id=transaction_id,
            command=command,
            user_ref=source_preview.owner_ref,
            status=status,
            assessment=assessment,
            occurred_at=occurred_at,
        )
        session.add(transaction)
        await session.flush()

        if decision == "APPROVE":
            await ledger.post_transfer_entries(
                session,
                transaction=transaction,
                source=source,
                destination=destination,
                amount_minor=command.amount_minor,
            )

        body = _response_body(transaction).model_dump(mode="json")
        return (
            PaymentOutcome(
                body_json=json.dumps(body, separators=(",", ":")),
                status_code=status_code,
                replayed=False,
                transaction_id=transaction.id,
            ),
            transaction,
        )

    async def _assess(
        self,
        command: PaymentCommand,
        source: Account,
        transaction_id: uuid.UUID,
        occurred_at: datetime,
    ) -> RiskAssessment | None:
        if self._risk is None:
            return None
        return await self._risk.assess(
            AssessmentRequest(
                transaction_id=transaction_id,
                user_ref=source.owner_ref,
                amount_minor=command.amount_minor,
                home_country=source.home_country,
                country=command.country,
                merchant_id=command.merchant_id,
                occurred_at=occurred_at,
            )
        )

    @staticmethod
    def _require_same_currency(
        command: PaymentCommand, source: Account, destination: Account
    ) -> None:
        if (
            source.currency.strip() != destination.currency.strip()
            or source.currency.strip() != command.currency
        ):
            raise CurrencyMismatch(
                "accounts and request must share one currency",
                request_currency=command.currency,
                source_currency=source.currency.strip(),
                destination_currency=destination.currency.strip(),
            )


def _new_transaction(
    *,
    transaction_id: uuid.UUID,
    command: PaymentCommand,
    user_ref: str,
    status: str,
    assessment: RiskAssessment | None,
    occurred_at: datetime,
) -> Transaction:
    return Transaction(
        id=transaction_id,
        from_account_id=command.from_account_id,
        to_account_id=command.to_account_id,
        user_ref=user_ref,
        amount_minor=command.amount_minor,
        currency=command.currency,
        country=command.country,
        merchant_id=command.merchant_id,
        device_id=command.device_id,
        status=status,
        description=command.description,
        decision=assessment.decision if assessment else "APPROVE",
        risk_score=assessment.score if assessment else None,
        model_version=assessment.model_version if assessment else "disabled",
        rules_fired=list(assessment.rules_fired) if assessment else [],
        features=assessment.features if assessment else None,
        velocity_degraded=assessment.degraded if assessment else False,
        review_status="pending" if status == "held" else None,
        scored_at=assessment.scored_at if assessment else None,
        created_at=occurred_at,
    )


def _response_body(transaction: Transaction) -> TransactionResponse:
    return TransactionResponse(
        id=transaction.id,
        status=transaction.status,
        from_account_id=transaction.from_account_id,
        to_account_id=transaction.to_account_id,
        amount_minor=transaction.amount_minor,
        currency=transaction.currency.strip(),
        description=transaction.description,
        created_at=transaction.created_at,
        decision=transaction.decision,
        risk_score=transaction.risk_score,
        model_version=transaction.model_version,
        rules_fired=list(transaction.rules_fired or []),
        review_status=transaction.review_status,
    )


async def _lock_accounts_in_order(
    session: AsyncSession, account_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Account]:
    """Lock every participant in ascending id order, one statement each.

    Two statements rather than a single ``WHERE id = ANY(...) ORDER BY id FOR
    UPDATE``: Postgres does not promise the sort happens before rows are
    locked, so the ordered-set form looks safe and is not. With a deterministic
    order, A->B and B->A can never deadlock.

    ``populate_existing`` is load-bearing: the scoring preview above loaded
    these objects into the identity map without a lock, and without this flag
    SQLAlchemy would keep those stale attribute values instead of the locked
    row's — every concurrent transfer would then compute from the same stale
    balance and silently lose the other's write.
    """
    locked: dict[uuid.UUID, Account] = {}
    for account_id in sorted(set(account_ids)):
        account = await session.scalar(
            select(Account)
            .where(Account.id == account_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if account is None:
            raise AccountNotFound(f"account {account_id} not found", account_id=str(account_id))
        locked[account_id] = account
    return locked


__all__ = ["PaymentCommand", "PaymentOutcome", "PaymentService"]
