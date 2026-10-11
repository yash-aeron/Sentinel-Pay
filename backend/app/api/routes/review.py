"""Analyst review surface: decide held payments, guarded by the analyst token.

Both endpoints are idempotent: re-sending a decision that already happened
returns the current transaction state with ``200``, so a retry after a
network blip cannot double-settle. The response body carries
``status``/``review_status``, which is what the dashboard renders.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import AnalystDep, ReviewDep
from app.schemas.transaction import TransactionResponse
from app.services.review import ACTION_APPROVE, ACTION_REJECT

router = APIRouter(tags=["review"])


@router.post(
    "/transactions/{transaction_id}/approve",
    response_model=TransactionResponse,
    responses={
        200: {"description": "Payment settled (or the decision had already been taken)"},
        401: {"description": "Missing or wrong X-Analyst-Token"},
        404: {"description": "Unknown transaction"},
        409: {"description": "Source account no longer has funds; the payment stays held"},
    },
)
async def approve_transaction(
    transaction_id: uuid.UUID, review: ReviewDep, analyst: AnalystDep
) -> TransactionResponse:
    return await review.decide(transaction_id, ACTION_APPROVE, analyst)


@router.post(
    "/transactions/{transaction_id}/reject",
    response_model=TransactionResponse,
    responses={
        200: {"description": "Payment declined (or the decision had already been taken)"},
        401: {"description": "Missing or wrong X-Analyst-Token"},
        404: {"description": "Unknown transaction"},
    },
)
async def reject_transaction(
    transaction_id: uuid.UUID, review: ReviewDep, analyst: AnalystDep
) -> TransactionResponse:
    return await review.decide(transaction_id, ACTION_REJECT, analyst)


__all__ = ["router"]
