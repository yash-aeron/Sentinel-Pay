---
id: POL-006
title: Audit Trail and Retention
jurisdiction: global
effective_date: 2026-03-01
---

# Audit Trail and Retention

## Purpose

Two records exist for every payment: the ledger in Postgres, which is the
source of truth for money, and the decision audit in MongoDB, which is the
forensic record of why it was scored the way it was.

## What the audit holds

Each audit document records the decision, the risk score, the model
version, the rules that fired, the full feature vector, and whether the
velocity store was degraded at scoring time. An auditor must be able to
reconstruct the decision years later from these documents alone.

## Reconciliation

A settled payment with no audit document is a reportable condition. The
reconciliation script reports the gap and can backfill the missing
documents from Postgres. Reconciliation must be run after any audit store
outage.

## Idempotent writes

The audit document is keyed by transaction id and written as an idempotent
upsert after the ledger commits. A retry after an audit outage therefore
cannot create a second copy, and a payment that settled must never be
rolled back because the audit write failed: the client retries with the
same idempotency key and the audit is healed.

## Retention

Audit documents are append-only. Corrections are new documents, never
edits.
