---
id: POL-004
title: Amount Anomalies and Cash-Out Patterns
jurisdiction: global
effective_date: 2026-02-01
---

# Amount Anomalies and Cash-Out Patterns

## Purpose

Compromised accounts are usually monetized by a single large transfer after
a period of ordinary activity. This policy defines the amount-contrast
signal.

## The eight-times rule

A payment of at least 8 times the average of the user's prior attempts in
the trailing 10 minute window, given at least 3 prior attempts, must be held
for review. The comparison is against the *prior* average, excluding the
current attempt, so the rule measures a change in behaviour rather than the
absolute size.

## Why contrast and not size

A large payment from an account whose recent history is large payments is
ordinary. Only the contrast with history is anomalous, so a payment's
absolute amount alone must never trigger this rule.

## Interaction with the model

The amount-contrast signal is also a model feature. The rule exists so the
behaviour is caught even when the model is missing, degraded, or simply
wrong; the rule floor cannot be lowered by a calm model score.
