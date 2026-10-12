"""Build the long-lived services once, at application startup."""

from __future__ import annotations

from dataclasses import dataclass

import redis.asyncio as aioredis
from pymongo import AsyncMongoClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import get_logger
from app.services.audit import AuditStore
from app.services.copilot.corpus import load_corpus
from app.services.copilot.llm import build_llm
from app.services.copilot.service import CopilotService
from app.services.copilot.store import ChromaPolicyStore, build_embeddings
from app.services.events import EventBus
from app.services.payments import PaymentService
from app.services.review import ReviewService
from app.services.risk.decision import RiskPolicy
from app.services.risk.engine import RiskEngine
from app.services.risk.model import RiskModel, load_risk_model
from app.services.velocity import VelocityStore

log = get_logger(__name__)


@dataclass(slots=True)
class Services:
    payments: PaymentService
    review: ReviewService
    events: EventBus
    redis: aioredis.Redis | None = None
    mongo: AsyncMongoClient[dict[str, object]] | None = None
    velocity: VelocityStore | None = None
    model: RiskModel | None = None
    audit: AuditStore | None = None
    copilot: CopilotService | None = None


async def build_services(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    risk_enabled: bool | None = None,
    copilot_enabled: bool | None = None,
) -> Services:
    enabled = settings.risk_enabled if risk_enabled is None else risk_enabled

    if not enabled:
        # Explicitly risk-off: no Redis or Mongo connection is opened, so unit
        # and concurrency tests never depend on either service.
        log.warning("risk_disabled", reason="configured off")
        events = EventBus()
        return Services(
            payments=PaymentService(sessionmaker, events=events),
            review=ReviewService(sessionmaker, events=events),
            events=events,
        )

    redis = aioredis.Redis.from_url(settings.redis_url, decode_responses=False)
    mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(settings.mongo_url)

    velocity = VelocityStore(redis)
    model = load_risk_model(settings.risk_model_file, settings.risk_model_meta_file)
    engine = RiskEngine(
        velocity,
        model,
        RiskPolicy(
            approve_max=settings.risk_approve_max,
            review_max=settings.risk_review_max,
        ),
    )
    audit = AuditStore(mongo, settings.mongo_db)
    await audit.ensure_indexes()

    events = EventBus()
    payments = PaymentService(sessionmaker, risk=engine, audit=audit, events=events)
    review = ReviewService(sessionmaker, events=events)

    copilot = build_copilot(
        settings, sessionmaker, enabled=settings.copilot_enabled
        if copilot_enabled is None else copilot_enabled
    )
    log.info(
        "risk_services_ready",
        model_version=model.version,
        redis_url=settings.redis_url,
        mongo_db=settings.mongo_db,
        copilot="on" if copilot is not None else "off",
    )
    return Services(
        payments=payments,
        review=review,
        events=events,
        redis=redis,
        mongo=mongo,
        velocity=velocity,
        model=model,
        audit=audit,
        copilot=copilot,
    )


def build_copilot(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    enabled: bool,
) -> CopilotService | None:
    """The copilot is optional: no corpus or an explicit off keeps it None."""
    if not enabled or not settings.policies_dir.is_dir():
        log.warning("copilot_disabled", reason="no policy corpus or disabled")
        return None
    store = ChromaPolicyStore(
        settings.chroma_dir, build_embeddings(settings.copilot_embeddings)
    )
    indexed = store.index(load_corpus(settings.policies_dir))
    llm = build_llm(
        settings.llm_provider,
        {
            "base_url": settings.ollama_base_url,
            "model": settings.ollama_model,
            "timeout_seconds": settings.ollama_timeout_seconds,
        },
    )
    log.info(
        "copilot_ready",
        chunks=indexed,
        embeddings=store.embedding_name,
        llm=llm.name,
    )
    return CopilotService(store, llm, sessionmaker)


async def close_services(services: Services) -> None:
    if services.redis is not None:
        await services.redis.aclose()
    if services.mongo is not None:
        await services.mongo.close()


__all__ = ["Services", "build_services", "close_services"]
