---
id: POL-007
title: Idempotency and Exactly-Once Movement
jurisdiction: global
effective_date: 2026-01-15
---

# Idempotency and Exactly-Once Movement

## Purpose

Clients retry. Networks fail. This policy defines the contract that makes
retries safe for money movement.

## The key

Every payment request carries an Idempotency-Key of 8 to 255 characters.
The same key with the same body replays the original response without a
second movement. The same key with a different body is refused with an
idempotency_key_reuse error: refusing is the only safe answer when the
alternative is guessing between double-charging and silently ignoring.

## Concurrency

Duplicate requests serialise on an advisory lock scoped to the key. Fifty
simultaneous retries produce one writer and forty-nine replayers, not a
retry storm. The lock is transaction-scoped, so a crashed request releases
it automatically.

## Scope

Keys are scoped per API client, so two clients cannot collide on the same
string. A client that reuses its own key for a genuinely different payment
gets an error, not a duplicate charge.

## Ledger invariants

Per transaction, debits equal credits. Globally, signed entries sum to
zero. The overdraft guard lives in the database schema as well as the
service layer, so a future code path cannot quietly create a negative
balance.
