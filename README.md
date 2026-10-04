# SentinelPay

Idempotent, double-entry payment engine with real-time fraud scoring and an
analyst dashboard.

Status: **Phase 1 of 5 complete** (payment core). Phases 2-5 are in progress;
see [Roadmap](#roadmap).

---

## What makes this interesting

Most "payment API" demos get the happy path right and quietly lose money on the
interesting paths. The parts worth reading are:

- **Idempotency that survives concurrency.** 50 simultaneous retries of the same
  request produce exactly one ledger movement. Same key with a mutated body is
  rejected rather than silently replayed.
- **Ledger invariants enforced by the database.** Not just "the service checks
  the balance" — a `CHECK` constraint refuses an overdraft even for a direct
  `UPDATE`.
- **Deterministic lock ordering.** `A -> B` and `B -> A` in the same instant
  cannot deadlock, proven by a test that runs 100 of them concurrently.

Full rationale: [`docs/design.md`](docs/design.md).

## Quickstart

Prerequisites: Docker (running), Python 3.11+.

```bash
docker compose up -d postgres redis mongo

python -m venv ~/.venvs/sentinelpay
~/.venvs/sentinelpay/Scripts/python -m pip install -e "backend[dev]"   # Windows
# source ~/.venvs/sentinelpay/bin/activate && pip install -e "backend[dev]"  # macOS/Linux

cp .env.example .env        # change the passwords before doing anything real
cd backend
alembic upgrade head
python scripts/seed.py
uvicorn app.main:app --reload --port 8000
```

Interactive API docs: <http://127.0.0.1:8000/docs>

## API

### `POST /transactions`

Requires an `Idempotency-Key` header (8-255 chars).

```bash
curl -X POST http://127.0.0.1:8000/transactions \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: 4d1c2f0a-transfer-001" \
  -d '{
    "from_account_id": "<alice>",
    "to_account_id": "<bob>",
    "amount_minor": 2500,
    "currency": "USD",
    "description": "invoice 1042"
  }'
```

| Situation | Response |
|---|---|
| New key | `201` with the settled transaction |
| Same key, same body | original `201` body, `Idempotent-Replay: true`, **no second charge** |
| Same key, different body | `422 idempotency_key_reuse` |
| Insufficient funds | `409 insufficient_funds`, no ledger rows |

Money is always integer minor units (2500 = 25.00 USD). No floats, no rounding.

## Tests

The concurrency suite runs against **real PostgreSQL** — locking behaviour
cannot be faked with SQLite.

```bash
cd backend
pytest                                   # all
pytest -m concurrency -v                 # the money-critical ones
pytest --cov=app --cov-report=term-missing
```

| Test | Proves |
|---|---|
| `test_fifty_concurrent_duplicates_move_money_once` | exactly one movement, 49 replays |
| `test_same_key_different_body_is_rejected` | mutated retry cannot double-charge |
| `test_opposing_transfers_never_deadlock` | 100 concurrent opposite transfers, zero deadlocks |
| `test_ledger_sums_to_zero_after_random_traffic` | per-transaction and global invariants |
| `test_database_refuses_an_overdraft_even_without_the_service` | the `CHECK` is a real backstop |

Independent integrity check against a live database:

```bash
python scripts/verify_ledger.py
```

## Roadmap

| Phase | Scope | State |
|---|---|---|
| 1 | Idempotent double-entry payment core | **done** |
| 2 | Redis velocity windows, IsolationForest + rules, APPROVE/REVIEW/DECLINE, Mongo audit | next |
| 3 | React analyst dashboard with SSE feed, flagged queue, review actions | planned |
| 4 | RAG compliance copilot citing policy + features (Chroma + Ollama) | planned |
| 5 | Observability, rate limiting, Locust results, demo script | planned |

## Layout

```
backend/app/
  api/routes/     HTTP surface
  services/       transfer orchestration, idempotency, double-entry posting
  db/models/      accounts, transactions, ledger_entries, idempotency_keys
  core/           settings, error taxonomy, logging
backend/tests/
  concurrency/    the tests that actually matter
  unit/           pure functions
policies/         synthetic compliance corpus (Phase 4)
docs/design.md    decisions, failure modes, trade-offs
```

## Local infrastructure notes

- WSL2 is capped at 6 GB via `~/.wslconfig`; this laptop has 15.4 GB total.
- Redis runs with `maxmemory-policy noeviction` on purpose: silently evicting
  velocity counters would blind the fraud layer.
- Chroma runs embedded in the API process and Ollama runs natively on Windows
  (GPU passthrough to WSL2 on laptop dGPUs is unreliable). See `docs/design.md`.
