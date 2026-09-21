"""Data-drift monitoring on the CDC price stream.

The model is trained on a fixed reference distribution of car prices; the
CDC pipeline keeps producing fresh observations. When the live distribution
of prices (and the engineered numeric features) drifts far enough from the
reference, the model's assumptions decay and retraining should be
considered.

We use the Population Stability Index (PSI): for each monitored feature the
live observations are bucketed into the reference quantile bins and

    PSI = sum over bins of (live_share - ref_share) * ln(live_share / ref_share)

Conventional thresholds (borrowed from credit-risk practice): PSI < 0.1 is
stable, 0.1-0.25 warrants investigation, > 0.25 is significant drift. The
aggregate flag here triggers when any single feature exceeds the action
threshold — a deliberately sensitive rule for a small, price-only system.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.features import CURRENT_YEAR, MODEL_DIR, engineer_features

logger = logging.getLogger(__name__)

# PSI action threshold: above this, retraining should be scheduled.
PSI_RETRAIN_THRESHOLD = 0.25
# Warn threshold: drift is emerging, keep watching closely.
PSI_WARN_THRESHOLD = 0.10
# Minimum live rows for a statistically meaningful comparison.
MIN_LIVE_ROWS = 30
# Quantile bins for PSI (quartiles + edges).
N_BINS = 4
EPSILON = 1e-6  # guards the log against empty bins

REFERENCE_PATH = os.path.join(MODEL_DIR, "drift_reference.json")

# Numeric features monitored for drift (engineered at scoring time).
MONITORED_FEATURES = ["price_jpy", "car_age", "mileage_per_year", "engine_cc"]


def _bin_edges(values: pd.Series) -> List[float]:
    """Interior quantile edges of the reference distribution (quartiles)."""
    return [
        float(edge)
        for edge in values.quantile([i / N_BINS for i in range(1, N_BINS)])
        .tolist()
    ]


def _shares(values: pd.Series, edges: List[float]) -> List[float]:
    """Share of observations per quantile bucket defined by ``edges``."""
    if values.empty:
        return [0.0] * (len(edges) + 1)
    buckets = np.searchsorted(edges, values.to_numpy(), side="right")
    counts = np.bincount(buckets, minlength=len(edges) + 1)
    return (counts / len(values)).tolist()


def _psi_from_shares(ref_shares: List[float], live_shares: List[float]) -> float:
    """PSI between two share vectors (the standard bucketed formula)."""
    psi = 0.0
    for ref, live in zip(ref_shares, live_shares):
        ref = max(ref, EPSILON)
        live = max(live, EPSILON)
        psi += (live - ref) * np.log(live / ref)
    return float(psi)


def capture_reference(frame: pd.DataFrame, model_version: str) -> Dict[str, Any]:
    """Build and persist the drift reference from a training frame.

    Stores per-feature quantile edges and bucket shares so the exact
    reference distribution can be reconstructed at comparison time.
    Called automatically by ``train_and_save_model``.
    """
    data = engineer_features(frame)
    features: Dict[str, Any] = {}
    for name in MONITORED_FEATURES:
        series = pd.to_numeric(data[name], errors="coerce").dropna()
        if series.empty:
            continue
        edges = _bin_edges(series)
        features[name] = {
            "edges": edges,
            "shares": _shares(series, edges),
            "mean": float(series.mean()),
        }
    reference = {
        "model_version": model_version,
        "captured_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "rows": int(len(data)),
        "features": features,
    }
    with open(REFERENCE_PATH, "w", encoding="utf-8") as handle:
        json.dump(reference, handle, indent=2)
    logger.info(
        "Drift reference captured for model %s (%d rows, %d features).",
        model_version, len(data), len(features),
    )
    return reference


def load_reference() -> Optional[Dict[str, Any]]:
    """Load the persisted drift reference, or None when absent/corrupt."""
    if not os.path.exists(REFERENCE_PATH):
        return None
    try:
        with open(REFERENCE_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (ValueError, OSError) as exc:
        logger.warning("Could not read drift reference: %s", exc)
        return None


def compute_psi(reference: Dict[str, Any], live: pd.DataFrame) -> Dict[str, Any]:
    """PSI per monitored feature between the reference and a live frame.

    Returns a dict with per-feature PSI values, the worst feature, and the
    overall verdict — the exact shape served by ``/api/drift``.
    """
    data = engineer_features(live)
    results: Dict[str, Any] = {}
    worst_name, worst_psi = None, -1.0
    for name in MONITORED_FEATURES:
        spec = reference.get("features", {}).get(name)
        if spec is None:
            continue
        series = pd.to_numeric(data[name], errors="coerce").dropna()
        if series.empty:
            results[name] = {"psi": None, "status": "no_data"}
            continue
        psi = _psi_from_shares(spec["shares"], _shares(series, spec["edges"]))
        results[name] = {
            "psi": round(psi, 4),
            "status": (
                "drift" if psi > PSI_RETRAIN_THRESHOLD
                else "warn" if psi > PSI_WARN_THRESHOLD
                else "stable"
            ),
        }
        if psi > worst_psi:
            worst_name, worst_psi = name, psi

    drift = worst_psi > PSI_RETRAIN_THRESHOLD
    return {
        "model_version": reference.get("model_version"),
        "live_rows": int(len(data)),
        "min_rows_for_check": MIN_LIVE_ROWS,
        "enough_data": len(data) >= MIN_LIVE_ROWS,
        "thresholds": {"warn": PSI_WARN_THRESHOLD, "retrain": PSI_RETRAIN_THRESHOLD},
        "features": results,
        "worst_feature": worst_name,
        "retraining_recommended": bool(drift and len(data) >= MIN_LIVE_ROWS),
        "current_year": CURRENT_YEAR,
    }


def drift_report(live: pd.DataFrame) -> Dict[str, Any]:
    """Full drift status for the API: never raises on missing pieces."""
    reference = load_reference()
    if reference is None:
        return {
            "status": "no_reference",
            "detail": (
                "No drift reference captured yet — it is written automatically "
                "when the model is trained."
            ),
        }
    if live is None or len(live) < MIN_LIVE_ROWS:
        return {
            "status": "insufficient_data",
            "detail": f"Need at least {MIN_LIVE_ROWS} live rows for a drift check.",
            "live_rows": 0 if live is None else int(len(live)),
            "model_version": reference.get("model_version"),
        }
    report = compute_psi(reference, live)
    report["status"] = (
        "drift_detected" if report["retraining_recommended"] else "stable"
    )
    return report
