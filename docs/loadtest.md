# Load test — 2026-10-24

Single uvicorn worker on the demo laptop (Ryzen 7 4800H, Windows 11), full
risk stack on: IsolationForest artifact loaded, Redis velocity, Mongo audit.
Rate limiting was lifted for the run (`RATE_LIMIT_PAYMENTS_PER_MINUTE=1000000`)
so the numbers measure the pipeline, not the limiter.

```
locust -f locustfile.py --headless -u 20 -r 5 -t 30s --csv locust
```

## Result

| Metric | Value |
|---|---|
| Payment throughput | 28.3 req/s sustained (38.5 req/s all endpoints) |
| `POST /transactions` p50 | 370 ms |
| `POST /transactions` p90 | 710 ms |
| `POST /transactions` p99 | 990 ms |
| Errors | **0** (risk decisions are successes; see locustfile) |
| Requests total | 1,122 in 30 s |

Reads are an order of magnitude cheaper (`GET /transactions` p50 150 ms)
because they skip scoring, velocity, and the audit write.

## Reading the numbers honestly

- The payment path does real work per request: a Lua-script round trip to
  Redis, an IsolationForest inference, a locked double-entry transaction, and
  a Mongo upsert. 370 ms median at 28 rps on one worker is the *correct*
  ball-park for that pipeline on a laptop; it is not a production number.
- Declines (402) are treated as successes in the scenarios because they are
  decisions. In the runs, roughly a third of the synthetic traffic was
  declined or held — the load pattern (rapid small payments) is exactly what
  the velocity rules exist to catch.
- The concurrency suite (`pytest -m concurrency`) is the meaningful
  correctness check under contention: 100 opposing transfers with zero
  deadlocks, 50 duplicate requests with exactly one movement.

## Known limits (documented, not discovered)

- One worker: the SSE feed and both middlewares are in-process. Phase-out is
  `LISTEN/NOTIFY` for events and Redis-backed limiter buckets, per
  `docs/design.md` §6.
- Locking is per-account-row; two users never contend, which is why
  per-user scenario pairs were used to isolate API cost from lock queueing.
