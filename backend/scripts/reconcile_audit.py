"""Find Postgres transactions that never made it into the Mongo audit log.

The audit write happens after the money commits, so a Mongo outage can leave a
settled payment unaudited. That asymmetry is detectable by design; this script
is the detector. It reports — and, with ``--backfill``, repairs — the gap.

    python scripts/reconcile_audit.py            # report only
    python scripts/reconcile_audit.py --backfill # also write the missing docs
"""

from __future__ import annotations

import argparse
import asyncio

from pymongo import AsyncMongoClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Transaction
from app.db.session import create_engine, create_sessionmaker
from app.services.audit import AuditStore, transaction_document

MongoDocument = AsyncMongoClient[dict[str, object]]


async def missing_ids(session: AsyncSession, audit: AuditStore) -> list[str]:
    rows = (
        await session.scalars(select(Transaction).order_by(Transaction.created_at))
    ).all()
    missing: list[str] = []
    for row in rows:
        if await audit.get(str(row.id)) is None:
            missing.append(str(row.id))
    return missing


async def main(backfill: bool) -> int:
    settings = get_settings()
    mongo: MongoDocument = AsyncMongoClient(settings.mongo_url)
    audit = AuditStore(mongo, settings.mongo_db)

    engine = create_engine(settings.database_url, pool_size=2)
    sessionmaker = create_sessionmaker(engine)
    try:
        async with sessionmaker() as session:
            missing = await missing_ids(session, audit)
    finally:
        await engine.dispose()

    if not missing:
        await mongo.close()
        print("ok: every Postgres transaction has an audit document")
        return 0

    print(f"{len(missing)} transaction(s) without an audit document:")
    for transaction_id in missing:
        print(f"  {transaction_id}")

    if not backfill:
        await mongo.close()
        print("\nrun with --backfill to write the missing documents")
        return 1

    try:
        for transaction_id in missing:
            async with sessionmaker() as session:
                row = await session.get(Transaction, transaction_id)
                if row is None:  # deleted between the two passes; skip
                    continue
                await audit.upsert(transaction_document(row))
                print(f"backfilled {transaction_id}")
    finally:
        await engine.dispose()
        await mongo.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backfill", action="store_true", help="write the missing audit documents"
    )
    raise SystemExit(asyncio.run(main(parser.parse_args().backfill)))
