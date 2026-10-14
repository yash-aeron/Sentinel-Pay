---
id: POL-002
title: Geographic Hopping and Cross-Border Controls
jurisdiction: global
effective_date: 2026-02-01
---

# Geographic Hopping and Cross-Border Controls

## Purpose

Credential theft often presents as one account transacting from several
countries in quick succession. This policy sets the geographic signals and
the responses they demand.

## Geographic hopping

Three or more distinct countries within a 60 second window for one user
requires the payment to be held for review. Two countries are normal travel
behaviour and must not be escalated on their own.

## Large cross-border payments

A payment of 100000 minor units ($1,000.00) or more that originates outside
the account holder's home country must be held for review regardless of the
model score. Amounts below that floor follow the ordinary model decision.

## Home country

The account holder's home country is recorded at account creation and is
used only as the comparison baseline for these controls. It is not a
compliance attribute and must not be used for sanctions screening, which is
run by a separate system.

## Records

Every geographic hold must record the countries observed in the velocity
window so an analyst can reconstruct the pattern without re-querying the
velocity store.
