---
id: POL-005
title: Model Governance and Artifact Control
jurisdiction: global
effective_date: 2026-03-01
---

# Model Governance and Artifact Control

## Purpose

The anomaly model is one input to the decision, never the decision itself.
This policy governs how the model is trained, versioned, and served.

## Training

The model trains on synthetic normal traffic through the same feature
builder the API serves with. Serving and training must share one feature
contract; the artifact records the exact feature names and order it was
trained on.

## Refusing to serve

Startup must refuse to load an artifact whose feature names disagree with
the serving code, or whose metadata disagrees with the artifact about its
version. A mismatched artifact would mis-score every payment silently, so
failing fast is the only safe behaviour. A missing artifact is a documented
setup state: the service runs rules-only.

## Calibration

The score is a ranking device, not a probability. Calibration anchors the
99th percentile of normal traffic at 0.5, and the approve band ends at 0.45
by default. The score must always be displayed alongside the model version
that produced it.

## Overrides

Deterministic rules may raise a decision to review or decline. No rule may
lower a decision, and no threshold tuning may convert a rule floor into an
approval.
