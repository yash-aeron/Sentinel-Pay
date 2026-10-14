---
id: POL-003
title: Review Holds and Analyst Decisions
jurisdiction: global
effective_date: 2026-02-01
---

# Review Holds and Analyst Decisions

## Purpose

A payment scored into the review band is held: no ledger movement occurs
until an analyst approves it. This policy governs those decisions.

## What a hold means

Holding reserves nothing. The source balance is unchanged, no entries are
posted, and the payment can still be rejected with zero cost. An approved
hold settles through the same double-entry machinery as an ordinary
payment, including a fresh funds check at approval time.

## Funds at approval

If the source account cannot cover the held amount when the analyst approves,
the approval must fail with an insufficient funds response and the payment
remains held. The analyst may then reject it or wait. Approvals must never
overdraft, even when the balance dropped after the payment was held.

## Idempotent decisions

Repeating an approve or reject on a decided payment returns the current
state and changes nothing. A decision is a state transition guarded by a row
lock, so two analysts acting at the same moment cannot settle a payment
twice.

## Recording

Every decision records the analyst identity, the action, and the resulting
status. Declined attempts remain in the transaction history permanently:
attempts are the unit of fraud analysis, not only settled money.
