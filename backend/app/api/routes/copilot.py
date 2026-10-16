"""Compliance copilot endpoint: grounded answers about a specific payment."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import AnalystDep
from app.core.errors import CopilotUnavailable

router = APIRouter(tags=["copilot"])


class CopilotQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: uuid.UUID
    question: str = Field(min_length=3, max_length=500)


class CopilotCitation(BaseModel):
    id: str
    doc_id: str
    title: str
    heading: str


class CopilotResponse(BaseModel):
    answer: str
    grounded: bool
    citations: list[CopilotCitation]
    retrieved: int
    llm: str


@router.post(
    "/copilot/ask",
    response_model=CopilotResponse,
    responses={
        200: {"description": "A grounded answer (or the insufficient-evidence reply)"},
        401: {"description": "Missing or wrong X-Analyst-Token"},
        503: {"description": "Copilot not configured, model down, or unknown transaction"},
    },
)
async def ask_copilot(
    payload: CopilotQuestion, request: Request, _analyst: AnalystDep
) -> CopilotResponse:
    copilot = request.app.state.copilot
    if copilot is None:
        raise CopilotUnavailable(
            "the copilot is not configured on this deployment "
            "(set COPILOT_ENABLED and provide policies/)"
        )
    answer = await copilot.answer(payload.transaction_id, payload.question)
    return CopilotResponse(
        answer=answer.answer,
        grounded=answer.grounded,
        citations=[CopilotCitation(**citation) for citation in answer.citations],
        retrieved=answer.retrieved,
        llm=answer.llm,
    )


__all__ = ["router"]
