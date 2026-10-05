"""Typed application errors.

Every failure that a client can meaningfully react to gets a stable machine code
so the dashboard, tests, and docs can talk about the same thing.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str = "", **context: Any) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.context = context


class AccountNotFound(AppError):
    status_code = 404
    code = "account_not_found"


class TransactionNotFound(AppError):
    status_code = 404
    code = "transaction_not_found"


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class SameAccountTransfer(AppError):
    status_code = 422
    code = "same_account_transfer"


class CurrencyMismatch(AppError):
    status_code = 422
    code = "currency_mismatch"


class IdempotencyKeyReuse(AppError):
    """Same key, different body. Refusing is the only safe answer: dtolnay's
    dilemma between double-charging and silently ignoring a request."""

    status_code = 422
    code = "idempotency_key_reuse"


class ConcurrentIdempotentRequest(AppError):
    """The same key is in flight. Callers should retry with the same key."""

    status_code = 409
    code = "concurrent_request_in_progress"


class InsufficientFunds(AppError):
    status_code = 409
    code = "insufficient_funds"


class AuditUnavailable(AppError):
    """The ledger committed but the audit store did not.

    Returning 503 is deliberate: the client retries with the same key, the
    replay path returns the original answer, and the audit upsert is retried
    because it is keyed on the transaction id.
    """

    status_code = 503
    code = "audit_unavailable"


class VelocityUnavailable(RuntimeError):
    """Redis could not answer. Internal: the risk engine degrades to REVIEW."""


class CopilotUnavailable(AppError):
    """The copilot cannot answer right now (no corpus, model down, or the
    requested transaction does not exist)."""

    status_code = 503
    code = "copilot_unavailable"


class LedgerIntegrityError(AppError):
    status_code = 500
    code = "ledger_integrity_error"
