"""The five-minute demo: ordinary payments, a fraud burst, a review, a copilot answer.

Runs against the local stack through the service layer (no HTTP), so it is
the same code the API serves. Assumes ``scripts/seed.py`` has been run and
``alembic upgrade head`` applied.

    python scripts/demo.py
"""

from __future__ import annotations

import asyncio
import json
import uuid

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Account
from app.db.session import create_engine, create_sessionmaker
from app.services.copilot.corpus import load_corpus
from app.services.copilot.llm import NullLLM
from app.services.copilot.service import CopilotService
from app.services.copilot.store import ChromaPolicyStore, build_embeddings
from app.services.payments import PaymentCommand
from app.services.wiring import build_services, close_services


def banner(title: str) -> str:
    return f"\n=== {title} ==="


async def _accounts(sessionmaker) -> dict[str, Account]:  # type: ignore[no-untyped-def]
    async with sessionmaker() as session:
        rows = (
            await session.scalars(
                select(Account).where(Account.account_type == "customer")
            )
        ).all()
        return {row.owner_ref: row for row in rows}


async def main() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_size=5)
    sessionmaker = create_sessionmaker(engine)
    services = await build_services(settings, sessionmaker)
    payments = services.payments
    review = services.review

    people = await _accounts(sessionmaker)
    alice, bob = people["alice"], people["bob"]
    print(banner("setup"))
    print(f"alice balance: {alice.balance_minor:>8} minor units")

    print(banner("1. an ordinary payment settles"))
    outcome = await payments.execute(
        PaymentCommand(
            scope="demo", idempotency_key=f"demo-ok-{uuid.uuid4()}",
            from_account_id=alice.id, to_account_id=bob.id,
            amount_minor=4_200, currency="USD", description="lunch split",
        )
    )
    body = json.loads(outcome.body_json)
    print(f"status={body['status']} decision={body['decision']} score={body['risk_score']}")

    print(banner("2. the same key replays byte-identically, no second charge"))
    replay = await payments.execute(
        PaymentCommand(
            scope="demo", idempotency_key="demo-fixed-replay-key",
            from_account_id=alice.id, to_account_id=bob.id,
            amount_minor=1_111, currency="USD",
        )
    )
    replay_twice = await payments.execute(
        PaymentCommand(
            scope="demo", idempotency_key="demo-fixed-replay-key",
            from_account_id=alice.id, to_account_id=bob.id,
            amount_minor=1_111, currency="USD",
        )
    )
    print(f"first: status={json.loads(replay.body_json)['status']}  "
          f"replay: replayed={replay_twice.replayed} id_matches="
          f"{json.loads(replay.body_json)['id'] == json.loads(replay_twice.body_json)['id']}")

    print(banner("3. card testing: six tiny probes in a minute -> blocked"))
    probe_outcomes = []
    for _ in range(6):
        outcome = await payments.execute(
            PaymentCommand(
                scope="demo", idempotency_key=f"demo-probe-{uuid.uuid4()}",
                from_account_id=bob.id, to_account_id=alice.id,
                amount_minor=100, currency="USD",
            )
        )
        probe_outcomes.append(json.loads(outcome.body_json))
    probe_body = probe_outcomes[-1]
    print(f"final probe: status={probe_body['status']} decision={probe_body['decision']} "
          f"rules={probe_body['rules_fired']}")

    print(banner("4. geo hopping: third country inside a minute"))
    for country in ("US", "FR"):
        await payments.execute(
            PaymentCommand(
                scope="demo", idempotency_key=f"demo-geo-{country}-{uuid.uuid4()}",
                from_account_id=alice.id, to_account_id=bob.id,
                amount_minor=2_000, currency="USD", country=country,
            )
        )
    flagged = await payments.execute(
        PaymentCommand(
            scope="demo", idempotency_key=f"demo-geo-DE-{uuid.uuid4()}",
            from_account_id=alice.id, to_account_id=bob.id,
            amount_minor=9_500, currency="USD", country="DE",
        )
    )
    flagged_body = json.loads(flagged.body_json)
    print(f"status={flagged_body['status']} review={flagged_body['review_status']} "
          f"decision={flagged_body['decision']} score={flagged_body['risk_score']} "
          f"rules={flagged_body['rules_fired']}")
    # The rule lifts the geo hop to at least REVIEW; the model can push it
    # further to DECLINE via decision = max(model, rule floor). Both outcomes
    # keep the money still, which is the actual guarantee.
    if flagged_body["status"] == "held":
        print("alice balance unchanged until a human decides:",
              (await _accounts(sessionmaker))["alice"].balance_minor)
        print(banner("5. the analyst approves; money moves through the ledger"))
        decision = await review.decide(
            uuid.UUID(flagged_body["id"]), "approve", "demo-analyst"
        )
        print(f"decision={decision.decision} status={decision.status} "
              f"review_status={decision.review_status}")
    else:
        print(banner("5. already declined by the model: nothing for an analyst to do"))
        decision = await review.decide(
            uuid.UUID(flagged_body["id"]), "reject", "demo-analyst"
        )
        print(f"reject on a decided payment is a no-op: status={decision.status}")

    print(banner("6. the copilot explains, grounded in the policy corpus"))
    settings = get_settings()
    # Same embedder the API builds with, so the persisted collection matches.
    store = ChromaPolicyStore(
        settings.chroma_dir, build_embeddings(settings.copilot_embeddings)
    )
    store.index(load_corpus(settings.policies_dir))
    copilot = CopilotService(store, NullLLM(), sessionmaker)
    answer = await copilot.answer(uuid.UUID(flagged_body["id"]), "why do geo hops get held?")
    print(f"llm={answer.llm} grounded={answer.grounded}")
    print(f"answer: {answer.answer[:220]}")
    for citation in answer.citations[:2]:
        print(f"  cited: {citation['id']} ({citation['title']} - {citation['heading']})")

    await close_services(services)
    await engine.dispose()
    print(banner("done"))


if __name__ == "__main__":
    asyncio.run(main())
