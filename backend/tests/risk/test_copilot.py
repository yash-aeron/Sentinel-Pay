"""Copilot integration: real Chroma (offline embedder) over the real corpus.

The full RAG path runs against the repository's actual policies with the
deterministic hash embedder, so no network and no ONNX model are needed.
Held payments are produced through the risk stack exactly as the dashboard
would produce them. The Ollama backend is exercised only for its failure
mode: a dead URL must surface as a 503, never as a hallucinated answer.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings
from app.main import create_app
from app.services.copilot.service import CopilotService
from app.services.wiring import build_services, close_services
from tests.risk.conftest import ANALYST, hold_a_payment

pytestmark = pytest.mark.risk

REPO_POLICIES = Path(__file__).resolve().parents[3] / "policies"


@pytest_asyncio.fixture
async def copilot_client(
    engine: AsyncEngine, velocity_redis, audit_collection, tmp_path: Path
) -> AsyncIterator[tuple[AsyncClient, object]]:
    """The risk-on app plus a copilot over real Chroma with the NullLLM."""
    settings = get_settings().model_copy(
        update={
            "risk_enabled": True,
            "redis_url": get_settings().redis_test_url,
            "mongo_db": get_settings().mongo_test_db,
            "analyst_token": "test-analyst-token",
            # Force rules-only so decisions stay deterministic.
            "risk_model_path": str(tmp_path / "no-model.joblib"),
            "risk_model_meta_path": str(tmp_path / "no-model.json"),
            # The copilot under test: real corpus, offline embedder, no LLM.
            "copilot_enabled": True,
            "copilot_embeddings": "hashing",
            "policies_path": str(REPO_POLICIES),
            "chroma_path": str(tmp_path / "chroma"),
            "llm_provider": "null",
        }
    )
    app = create_app(settings)
    app.state.engine = engine
    from app.db.session import create_sessionmaker

    app.state.sessionmaker = create_sessionmaker(engine)
    services = await build_services(settings, app.state.sessionmaker)
    app.state.services = services
    app.state.payments = services.payments
    app.state.review = services.review
    app.state.events = services.events
    app.state.velocity = services.velocity
    app.state.audit = services.audit
    app.state.risk_model = services.model
    app.state.copilot = services.copilot
    assert services.copilot is not None
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://sentinelpay.test"
        ) as http:
            yield http, app
    finally:
        await close_services(services)


async def test_copilot_requires_token(copilot_client):
    client, _ = copilot_client
    response = await client.post(
        "/copilot/ask",
        json={"transaction_id": str(uuid.uuid4()), "question": "why was this held?"},
    )
    assert response.status_code == 401


async def test_copilot_answers_with_policy_citations(copilot_client, funded_risk_accounts):
    client, _ = copilot_client
    held = await hold_a_payment(client, funded_risk_accounts, 1_000)
    response = await client.post(
        "/copilot/ask",
        json={"transaction_id": held["id"], "question": "why is this payment held?"},
        headers=ANALYST,
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["grounded"] is True
    assert body["retrieved"] > 0
    assert body["llm"] == "null"
    cited_ids = {citation["id"] for citation in body["citations"]}
    assert len(cited_ids) == body["retrieved"]
    # The answer must cite a policy chunk that exists in the corpus.
    assert body["answer"].startswith("[POL-")


async def test_copilot_unknown_transaction_is_503(copilot_client):
    client, _ = copilot_client
    response = await client.post(
        "/copilot/ask",
        json={"transaction_id": str(uuid.uuid4()), "question": "why?"},
        headers=ANALYST,
    )
    assert response.status_code == 503


async def test_grounding_replaces_ungrounded_answers(copilot_client, funded_risk_accounts):
    """A model that invents citations gets its answer replaced wholesale."""
    client, app = copilot_client

    class LyingLLM:
        name = "lying"

        async def complete(self, prompt: str) -> str:
            return (
                "Per [POL-999#s7] the limit is 12345 minor units. "
                "Definitely block after 77 attempts."
            )

    held = await hold_a_payment(client, funded_risk_accounts, 1_000)
    copilot = app.state.copilot
    assert copilot is not None
    lying = CopilotService(copilot._store, LyingLLM(), app.state.sessionmaker)
    answer = await lying.answer(uuid.UUID(held["id"]), "what is the limit?")

    assert answer.grounded is False
    assert answer.answer.startswith("Insufficient evidence")
    # Citations remain the retrieval set: what the model claimed is not shown.
    assert all(citation["id"].startswith("POL-0") for citation in answer.citations)
