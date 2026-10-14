---
id: POL-001
title: Velocity Monitoring and Escalation
jurisdiction: global
effective_date: 2026-01-15
---

# Velocity Monitoring and Escalation

## Purpose

This policy defines how transaction velocity is monitored per user and when
escalation is mandatory. Velocity is measured across sliding windows of 1
minute, 10 minutes, and 1 hour, counting attempts, summed amount, distinct
countries, and distinct merchants.

## Thresholds

A user reaching 20 attempts within a 10 minute window must be blocked
outright. A user reaching 40 attempts within one hour must be reviewed even
if no single attempt is remarkable. Both thresholds are floors: the risk
engine may act earlier, but never later.

## Distinguishing card testing

Six or more attempts inside 60 seconds whose average amount is 300 minor
units ($3.00) or less constitutes card testing. Card testing must be blocked
immediately regardless of the model score, because the individual amounts are
too small for an anomaly model to rank reliably and the harm is realized
across the burst.

## Merchants

Eight distinct merchants within 10 minutes indicates merchant spraying and
requires review. A first-time merchant on its own is not suspicious and must
not be treated as evidence without a second signal.

## Failures

If the velocity store is unavailable, the payment must be held for review,
never approved silently. The absence of data is itself a reportable condition.
