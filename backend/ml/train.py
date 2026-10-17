"""Train the IsolationForest anomaly model and write the serving artifact.

    cd backend
    python -m ml.train [--samples N] [--seed S] [--out DIR]

The forest is fit on **normal traffic only** — anomaly detection, not
classification. Calibration quantiles (q50/q99 of the raw score on held-out
normal traffic) are what turn the model's unbounded output into the 0-1 score
the decision bands consume; see ``app/services/risk/model.py``.

The script exits non-zero when a fraud family fails to separate from normal
traffic. An artifact that cannot rank the shapes it was built for is worse
than no artifact, because it makes every APPROVE look informed.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import IsolationForest

from app.services.risk.features import FEATURE_NAMES
from app.services.risk.model import MODEL_VERSION, Calibration, IsolationForestModel
from ml import synthetic

DEFAULT_OUT = Path(__file__).resolve().parent / "artifacts"
FRAUD_FAMILIES = ("card_testing", "geo_hopping", "amount_spike")
REVIEW_THRESHOLD = 0.45  # matches the default RISK_APPROVE_MAX; reporting only


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=20_000, help="normal traffic rows")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="artifact directory")
    return parser.parse_args(argv)


def train(samples: int, seed: int) -> tuple[IsolationForest, Calibration, int, int]:
    normal = synthetic.generate("normal", samples, seed)
    split = int(len(normal) * 0.8)
    train_rows, calibration_rows = normal[:split], normal[split:]

    estimator = IsolationForest(n_estimators=200, random_state=seed)
    estimator.fit(synthetic.matrix(train_rows))

    # Same raw scale the serving path uses: negated score_samples.
    raw = -estimator.score_samples(synthetic.matrix(calibration_rows))
    calibration = Calibration(
        q50=float(np.quantile(raw, 0.50)),
        q99=float(np.quantile(raw, 0.99)),
    )
    return estimator, calibration, len(train_rows), len(calibration_rows)


def evaluate(
    estimator: IsolationForest, calibration: Calibration, seed: int
) -> dict[str, dict[str, float]]:
    model = IsolationForestModel(estimator=estimator, calibration=calibration)
    report: dict[str, dict[str, float]] = {}
    for label in ("normal", *FRAUD_FAMILIES):
        rows = synthetic.generate(label, 2_000, seed + 1)
        scores = np.asarray([model.score(row.features) for row in rows])
        report[label] = {
            "median": float(np.median(scores)),
            "share_review_plus": float(np.mean(scores >= REVIEW_THRESHOLD)),
        }
    return report


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    estimator, calibration, n_train, n_calibration = train(args.samples, args.seed)
    report = evaluate(estimator, calibration, args.seed)

    args.out.mkdir(parents=True, exist_ok=True)
    model_file = args.out / "model.joblib"
    meta_file = args.out / "model_meta.json"

    payload = {
        "version": MODEL_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "estimator": estimator,
        "calibration": {
            "q50": calibration.q50,
            "q99": calibration.q99,
            "k": calibration.k,
        },
    }
    joblib.dump(payload, model_file)

    meta = {
        "version": MODEL_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "trained_at": datetime.now(UTC).isoformat(),
        "sklearn_version": sklearn.__version__,
        "n_train": n_train,
        "n_calibration": n_calibration,
        "seed": args.seed,
        "calibration": payload["calibration"],
    }
    meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"model   {model_file}")
    print(f"meta    {meta_file}")
    print(f"calib   q50={calibration.q50:.4f}  q99={calibration.q99:.4f}  k={calibration.k}")
    print(f"{'family':<14} {'median':>8} {'% >= review':>12}")
    for label, stats in report.items():
        print(
            f"{label:<14} {stats['median']:>8.3f} {stats['share_review_plus'] * 100:>11.1f}%"
        )

    weak = [
        label
        for label in FRAUD_FAMILIES
        if report[label]["median"] < report["normal"]["median"] + 0.2
    ]
    if weak:
        print(
            f"\nREFUSING to certify: {', '.join(weak)} barely separates from "
            "normal traffic -- do not serve this artifact.",
            file=sys.stderr,
        )
        return 1
    print("\nartifact certified: every fraud family separates from normal traffic")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
