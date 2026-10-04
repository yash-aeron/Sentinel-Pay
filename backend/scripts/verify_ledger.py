"""Independent ledger integrity check.

Deliberately talks to Postgres with plain SQL instead of reusing the service
layer: if the service's own accounting rules were wrong, a checker built on
those rules would agree with them.

    python scripts/verify_ledger.py
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import create_engine

CHECKS: tuple[tuple[str, str], ...] = (
    (
        "every transaction balances",
        """
        SELECT COUNT(*) FROM (
            SELECT transaction_id
            FROM ledger_entries
            GROUP BY transaction_id
            HAVING SUM(CASE WHEN direction = 'credit' THEN amount_minor
                            ELSE -amount_minor END) <> 0
        ) offenders
        """,
    ),
    (
        "transactions have exactly two legs",
        """
        SELECT COUNT(*) FROM (
            SELECT transaction_id
            FROM ledger_entries
            GROUP BY transaction_id
            HAVING COUNT(*) <> 2
        ) offenders
        """,
    ),
    (
        "global signed sum is zero",
        """
        SELECT COALESCE(SUM(CASE WHEN direction = 'credit' THEN amount_minor
                                 ELSE -amount_minor END), 0)
        FROM ledger_entries
        """,
    ),
    (
        "balances match their entry history",
        """
        SELECT COUNT(*) FROM (
            SELECT a.id
            FROM accounts a
            LEFT JOIN ledger_entries e ON e.account_id = a.id
            GROUP BY a.id, a.balance_minor
            HAVING a.balance_minor <> COALESCE(SUM(
                CASE WHEN e.direction = 'credit' THEN e.amount_minor
                     ELSE -e.amount_minor END), 0)
        ) offenders
        """,
    ),
    (
        "no customer is overdrawn",
        """
        SELECT COUNT(*) FROM accounts
        WHERE account_type = 'customer' AND balance_minor < 0
        """,
    ),
)


async def main() -> int:
    engine = create_engine(get_settings().database_url, pool_size=2)
    failures = 0
    try:
        async with engine.connect() as conn:
            for name, sql in CHECKS:
                value = await conn.scalar(text(sql))
                ok = value == 0
                print(f"{'PASS' if ok else 'FAIL'}  {name:<36} {value}")
                failures += 0 if ok else 1
    finally:
        await engine.dispose()

    print("\nledger integrity:", "OK" if failures == 0 else f"{failures} check(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
