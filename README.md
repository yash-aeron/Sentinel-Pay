# SentinelPay

Idempotent, double-entry payment engine with real-time fraud scoring and an
analyst dashboard.

Status: **all five phases complete** — payment core, real-time risk engine,
analyst dashboard, RAG compliance copilot, and hardening (rate limiting,
metrics, load test). See [Roadmap](#roadmap).

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
- **Fraud scoring that never fails open.** Redis down, model missing, or an
  unexplainable request all end in `REVIEW` — a human sees it. Deterministic
  rules with stable ids (`R_CARD_TESTING`, `R_GEO_HOP_3_COUNTRIES_60S`, …)
  decide the unambiguous shapes regardless of the model's mood.
- **The model artifact is a contract.** Startup refuses to serve an artifact
  whose feature names disagree with the serving code — silent feature skew is
  the classic way ML systems fail without anyone noticing.
- **A copilot that cannot lie.** The RAG compliance copilot answers analyst
  questions from the policy corpus, but a validator — not a prompt — enforces
  grounding: any citation outside the retrieved set or any number not present
  in the evidence or the payment data replaces the answer with
  "insufficient evidence".

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
python -m ml.train          # trains the IsolationForest; optional, rules-only without it
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
| New key, risk says APPROVE | `201` with the settled transaction |
| New key, risk says REVIEW | `202`, transaction is `held`, **no money moved** until an analyst approves |
| New key, risk says DECLINE | `402`, transaction recorded, no money moved |
| Same key, same body | original body replayed, `Idempotent-Replay: true`, **no second charge** |
| Same key, different body | `422 idempotency_key_reuse` |
| Insufficient funds | `409 insufficient_funds`, no ledger rows |
| Committed but the Mongo audit failed | `503 audit_unavailable`; retry with the same key replays the original answer and re-attempts the audit |

Risk context is part of the request: `country` (ISO-3166 alpha-2, default `US`),
`merchant_id`, `device_id`. Money is always integer minor units
(2500 = 25.00 USD). No floats, no rounding.

The decision is `max(model score, rule floor)` banded by
`RISK_APPROVE_MAX`/`RISK_REVIEW_MAX`. A Redis outage during scoring is
`velocity_degraded=true` and forces `REVIEW` — never a silent `APPROVE`.

## Tests

The concurrency suite runs against **real PostgreSQL**, the risk suite against
**real Redis and MongoDB** — locking and windowing behaviour cannot be faked
with SQLite or in-memory doubles.

```bash
cd backend
pytest                                   # all
pytest -m concurrency -v                 # the money-critical ones
pytest -m risk -v                        # velocity, decisions, audit
pytest --cov=app --cov-report=term-missing
```

| Test | Proves |
|---|---|
| `test_fifty_concurrent_duplicates_move_money_once` | exactly one movement, 49 replays |
| `test_same_key_different_body_is_rejected` | mutated retry cannot double-charge |
| `test_opposing_transfers_never_deadlock` | 100 concurrent opposite transfers, zero deadlocks |
| `test_ledger_sums_to_zero_after_random_traffic` | per-transaction and global invariants |
| `test_database_refuses_an_overdraft_even_without_the_service` | the `CHECK` is a real backstop |
| `test_card_testing_burst_is_declined_and_moves_no_money` | the burst rule blocks the 6th probe in a minute |
| `test_velocity_outage_holds_payments_and_flags_degradation` | Redis down forces REVIEW, never APPROVE |
| `test_audit_failure_after_commit_returns_503_and_replays_cleanly` | money survives an audit outage; the retry heals the gap |

Independent integrity checks against a live database:

```bash
python scripts/verify_ledger.py     # double-entry invariants
python scripts/reconcile_audit.py   # every settled payment has an audit document
                                    # (--backfill repairs the gap)
```

## The model

IsolationForest trained on synthetic normal traffic (`backend/ml/synthetic.py`)
through the same feature builder the API serves with. Calibration maps the
raw anomaly score onto 0–1 so the 99th percentile of normal traffic lands at
0.5 — the score is a ranking device, not a probability. `python -m ml.train`
refuses to write an artifact whose fraud families do not separate from normal
traffic; without an artifact the API runs rules-only and stays correct.

## The dashboard

```bash
uvicorn app.main:app --port 8000        # terminal 1
cd frontend && npm install && npm run dev   # terminal 2 -> http://127.0.0.1:5173
```

Enter the analyst token (`ANALYST_TOKEN` in `.env`) and the dashboard connects
to the SSE feed: every payment attempt streams in live, held payments queue
for review with the rules that fired and the risk score, and approve/reject
settle or decline real money through the same ledger machinery as the API.
A reject of an already-decided payment is a no-op by construction.

## The copilot

`POST /copilot/ask` with an analyst token and a transaction id answers
"why was this held?" from `policies/*.md` — 9 synthetic policy documents,
chunked on headings, embedded into an embedded Chroma store. Grounding is
validated structurally (citations ⊆ retrieved set; numbers ⊆ evidence ∪
payment data) and an ungrounded answer is replaced wholesale. The LLM sits
behind a Protocol: `NullLLM` (deterministic, extractive, offline — used by
tests and CI) or `OllamaLLM` (local `qwen2.5:3b-instruct` on the GPU).

## Hardening

- **Rate limiting** — fixed-window, per-client budgets on
  `POST /transactions` and `POST /copilot/ask`
  (`RATE_LIMIT_*_PER_MINUTE`); 429 with `Retry-After`.
- **Metrics** — `GET /metrics` (analyst token) exposes Prometheus-format
  request counters by method/route/status with fixed label cardinality.
- **Health** — `/health/ready` pings Postgres, Redis, and Mongo but only the
  ones this deployment actually uses.
- **Load test** — see [`docs/loadtest.md`](docs/loadtest.md): 28 payments/s
  sustained with p99 under 1 s on one worker, zero errors.
- **Demo** — `python scripts/demo.py` walks the whole story: ordinary
  payment, idempotent replay, a card-testing burst, a geo hop, an analyst
  decision, and a grounded copilot answer.

## Roadmap

| Phase | Scope | State |
|---|---|---|
| 1 | Idempotent double-entry payment core | **done** |
| 2 | Redis velocity windows, IsolationForest + rules, APPROVE/REVIEW/DECLINE, Mongo audit | **done** |
| 3 | React analyst dashboard with SSE feed, flagged queue, review actions | **done** |
| 4 | RAG compliance copilot citing policy + features (Chroma + Ollama) | **done** |
| 5 | Observability, rate limiting, Locust results, demo script | **done** |

## Layout

```
backend/app/
  api/routes/     HTTP surface
  services/       payment orchestration, idempotency, double-entry posting,
                  review holds, event bus (SSE), velocity (Redis), audit (Mongo)
  services/risk/  features, rules, model loader, decision bands
  services/copilot/  corpus, vector store, LLM backends, grounding validator
  db/models/      accounts, transactions, ledger_entries, idempotency_keys
  core/           settings, error taxonomy, logging, rate limit + metrics
backend/ml/       synthetic traffic, trainer, model artifacts (generated)
backend/tests/    concurrency, risk (real Redis+Mongo), unit
frontend/         React analyst dashboard (Vite, strict TypeScript)
policies/         the compliance corpus the copilot cites
docs/             design decisions and the load-test report
```

## Local infrastructure notes

- WSL2 is capped at 6 GB via `~/.wslconfig`; this laptop has 15.4 GB total.
- Redis runs with `maxmemory-policy noeviction` on purpose: silently evicting
  velocity counters would blind the fraud layer.
- Chroma runs embedded in the API process and Ollama runs natively on Windows
  (GPU passthrough to WSL2 on laptop dGPUs is unreliable). See `docs/design.md`.
