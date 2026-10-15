---
id: POL-009
title: Customer Communication and Decline Handling
jurisdiction: US
effective_date: 2026-02-15
---

# Customer Communication and Decline Handling

## Purpose

Customers experience declines, not risk scores. This policy governs what
may be said about a decision and what must never be said.

## What may be said

A customer may be told that a payment was declined for security reasons
and asked to contact support. That is the whole script.

## What must never be said

The risk score, the model version, the rules that fired, and the feature
values are internal forensic data. Revealing them teaches fraudsters how
the decision was made. The analyst dashboard exists precisely so humans
see the full picture while customers see none of it.

## Held payments

A held payment must be described as pending, never as declined. No money
has moved and the customer may not be told a fraud system blocked them
until a decision exists.

## False positives

A false positive costs a legitimate payment; a false negative costs the
float. Analysts should reject cautiously and approve deliberately, and
every decline of an ordinary payment must list the rules that fired so
threshold tuning has evidence to work from.
