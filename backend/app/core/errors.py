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


class LedgerIntegrityError(AppError):
    status_code = 500
    code = "ledger_integrity_error"
