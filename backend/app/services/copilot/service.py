"""Copilot orchestration: transaction context -> retrieval -> LLM -> grounding.

The service exists to make the validator load-bearing. The LLM is a text
transform; the answer a user sees has either passed the grounding check or
been replaced with the insufficient-evidence string. Citations are always
the retrieval set, never the model's claims about what it used.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import CopilotUnavailable
from app.db.models import Transaction
from app.services.copilot.grounding import INSUFFICIENT, validate
from app.services.copilot.llm import LLM
from app.services.copilot.store import ChromaPolicyStore, Retrieved

PROMPT_TEMPLATE = """You are the SentinelPay compliance copilot. Answer the analyst's \
question about a payment using ONLY the policy excerpts in CONTEXT and the \
payment data in TRANSACTION.

Rules:
- Cite every policy excerpt you rely on as [chunk-id] on first use.
- Never state a number that is not in CONTEXT or TRANSACTION.
- If the excerpts do not answer the question, say exactly: {insufficient}

CONTEXT:
{context}

TRANSACTION:
{transaction}

QUESTION: {question}"""


@dataclass(frozen=True, slots=True)
class CopilotAnswer:
    answer: str
    grounded: bool
    citations: list[dict[str, str]]
    retrieved: int
    llm: str


class CopilotService:
    def __init__(
        self,
        store: ChromaPolicyStore,
        llm: LLM,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        k: int = 4,
    ) -> None:
        self._store = store
        self._llm = llm
        self._sessionmaker = sessionmaker
        self._k = k

    async def answer(self, transaction_id: uuid.UUID, question: str) -> CopilotAnswer:
        async with self._sessionmaker() as session:
            transaction = await session.get(Transaction, transaction_id)
        if transaction is None:
            raise CopilotUnavailable(
                f"transaction {transaction_id} not found",
                transaction_id=str(transaction_id),
            )

        # Rules that fired are the strongest retrieval hint: an analyst asking
        # "why held" wants the policy behind R_CARD_TESTING, not vibes.
        query = f"{question} {' '.join(transaction.rules_fired or [])}"
        retrieved = self._store.search(query, self._k)
        if not retrieved:
            return CopilotAnswer(
                answer=INSUFFICIENT,
                grounded=True,
                citations=[],
                retrieved=0,
                llm=self._llm.name,
            )

        context = _render_context(retrieved)
        transaction_json = json.dumps(
            _transaction_snapshot(transaction), indent=2, default=str
        )
        prompt = PROMPT_TEMPLATE.format(
            insufficient=INSUFFICIENT,
            context=context,
            transaction=transaction_json,
            question=question,
        )

        candidate = await self._llm.complete(prompt)
        grounded, _violations = validate(
            candidate,
            retrieved_ids={hit.chunk_id for hit in retrieved},
            retrieved_text=" ".join(hit.text for hit in retrieved),
            allowed_numbers=_allowed_numbers(transaction),
        )
        if not grounded:
            candidate = INSUFFICIENT

        return CopilotAnswer(
            answer=candidate,
            grounded=grounded,
            citations=[
                {
                    "id": hit.chunk_id,
                    "doc_id": hit.doc_id,
                    "title": hit.title,
                    "heading": hit.heading,
                }
                for hit in retrieved
            ],
            retrieved=len(retrieved),
            llm=self._llm.name,
        )


def _render_context(retrieved: list[Retrieved]) -> str:
    blocks = [
        f"[{hit.chunk_id}] {hit.title} - {hit.heading} "
        f"(jurisdiction={hit.jurisdiction}, effective {hit.effective_date})\n{hit.text}"
        for hit in retrieved
    ]
    return "\n\n".join(blocks)


def _transaction_snapshot(transaction: Transaction) -> dict[str, Any]:
    return {
        "transaction_id": str(transaction.id),
        "status": transaction.status,
        "decision": transaction.decision,
        "review_status": transaction.review_status,
        "amount_minor": transaction.amount_minor,
        "currency": transaction.currency.strip(),
        "country": transaction.country,
        "home_country_features": transaction.features,
        "rules_fired": list(transaction.rules_fired or []),
        "risk_score": transaction.risk_score,
        "model_version": transaction.model_version,
        "velocity_degraded": transaction.velocity_degraded,
    }


def _allowed_numbers(transaction: Transaction) -> set[str]:
    """Numbers the answer may use because they come from the payment itself."""
    allowed = {
        str(transaction.amount_minor),
        f"{transaction.amount_minor / 100:.2f}",
        str(transaction.risk_score) if transaction.risk_score is not None else "",
        str(transaction.velocity_degraded).lower(),
    }
    features = transaction.features or {}
    for value in features.values():
        allowed.add(str(value))
        allowed.add(f"{float(value):g}")
    return {item for item in allowed if item}


__all__ = ["CopilotAnswer", "CopilotService", "PROMPT_TEMPLATE"]
