"""Calibration math and the artifact contract the loader enforces."""

from __future__ import annotations

import json
import math
from pathlib import Path

import joblib
import pytest

from app.services.risk.features import FEATURE_NAMES
from app.services.risk.model import (
    MODEL_VERSION,
    Calibration,
    IsolationForestModel,
    RulesOnlyModel,
    load_risk_model,
)
from ml import synthetic


def test_calibration_anchors_median_and_p99_of_normal_traffic():
    calibration = Calibration(q50=0.40, q99=0.60, k=4.0)
    assert calibration.apply(0.40) == pytest.approx(1 / (1 + math.exp(4.0)))
    assert calibration.apply(0.60) == pytest.approx(0.5)


def test_calibration_is_monotone_and_saturates_without_overflow():
    calibration = Calibration(q50=0.0, q99=1.0, k=4.0)
    scores = [calibration.apply(raw) for raw in (-1e6, -1.0, 0.0, 1.0, 2.0, 1e6)]
    assert scores == sorted(scores)
    assert scores[0] >= 0.0 and scores[-1] <= 1.0
    assert all(math.isfinite(score) for score in scores)


def test_calibration_rejects_degenerate_quantiles():
    with pytest.raises(ValueError, match="q99"):
        Calibration(q50=0.5, q99=0.5)
    with pytest.raises(ValueError, match="q99"):
        Calibration(q50=0.6, q99=0.5)
    with pytest.raises(ValueError, match="k"):
        Calibration(q50=0.4, q99=0.6, k=0.0)


def test_rules_only_model_scores_zero():
    model = RulesOnlyModel()
    assert model.version == "rules-only"
    assert model.score(dict.fromkeys(FEATURE_NAMES, 0.0)) == 0.0


def _trained_payload(tmp_path: Path) -> tuple[Path, Path]:
    """Train a tiny real model through the same code path as ml.train."""
    from ml.train import train

    estimator, calibration, _, _ = train(600, seed=11)
    model_file = tmp_path / "model.joblib"
    meta_file = tmp_path / "model_meta.json"
    joblib.dump(
        {
            "version": MODEL_VERSION,
            "feature_names": list(FEATURE_NAMES),
            "estimator": estimator,
            "calibration": {"q50": calibration.q50, "q99": calibration.q99, "k": calibration.k},
        },
        model_file,
    )
    meta_file.write_text(
        json.dumps({"version": MODEL_VERSION, "feature_names": list(FEATURE_NAMES)}),
        encoding="utf-8",
    )
    return model_file, meta_file


def test_loader_returns_a_scoring_model_for_a_valid_artifact(tmp_path: Path):
    model_file, meta_file = _trained_payload(tmp_path)
    model = load_risk_model(model_file, meta_file)
    assert isinstance(model, IsolationForestModel)
    assert model.version == MODEL_VERSION

    normal = synthetic.generate("normal", 20, seed=3)[0].features
    card_testing = synthetic.generate("card_testing", 20, seed=3)[0].features
    assert 0.0 <= model.score(normal) <= 1.0
    assert model.score(card_testing) > model.score(normal)


def test_missing_artifact_falls_back_to_rules_only(tmp_path: Path):
    model = load_risk_model(tmp_path / "absent.joblib", tmp_path / "absent.json")
    assert isinstance(model, RulesOnlyModel)


def test_loader_refuses_a_feature_contract_mismatch(tmp_path: Path):
    model_file, meta_file = _trained_payload(tmp_path)
    payload = joblib.load(model_file)
    payload["feature_names"] = ["wrong", "names"]
    joblib.dump(payload, model_file)
    with pytest.raises(ValueError, match="contract mismatch"):
        load_risk_model(model_file, meta_file)


def test_loader_refuses_payload_and_meta_disagreeing_on_version(tmp_path: Path):
    model_file, meta_file = _trained_payload(tmp_path)
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    meta["version"] = "iforest-v0"
    meta_file.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        load_risk_model(model_file, meta_file)
