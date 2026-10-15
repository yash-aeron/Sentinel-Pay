---
id: POL-008
title: Degraded Modes and Fail-Safe Behaviour
jurisdiction: global
effective_date: 2026-03-01
---

# Degraded Modes and Fail-Safe Behaviour

## Purpose

Every supporting service can fail. This policy fixes what each failure
means for a payment, and the fixed point is that risk infrastructure never
fails open.

## Velocity store unavailable

If the velocity store cannot answer during scoring, the payment is held for
review with velocity_degraded set. It must never be approved silently: the
attacker who takes the velocity store down must not get a free pass.

## Model artifact unavailable

A missing artifact runs rules-only, which is correct because the rules are
deterministic and sufficient. A mismatched artifact is different: it must
stop startup, because serving it would corrupt every score.

## Audit store unavailable

A payment that has settled is not rolled back when the audit write fails.
The client receives an audit_unavailable error, retries with the same
idempotency key, gets the stored response, and the audit upsert is retried.
The gap is detectable by reconciliation.

## Redis eviction

The velocity store runs with no eviction policy. Silently evicting velocity
counters would blind the fraud layer, so a full store produces a loud error
rather than quiet blindness.
