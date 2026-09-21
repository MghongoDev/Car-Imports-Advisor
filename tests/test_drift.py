"""Tests for PSI-based drift monitoring (src/drift.py)."""

import pandas as pd
import pytest

from src.drift import (
    MIN_LIVE_ROWS,
    compute_psi,
    drift_report,
    load_reference,
)

# A reference distribution captured by training (see fixture below) is a
# dict: {"model_version": ..., "features": {name: {"edges", "shares", ...}}}.


@pytest.fixture(name="reference")
def fixture_reference(tmp_path, monkeypatch):
    """Capture a reference distribution from a stable synthetic frame."""
    monkeypatch.setattr(
        "src.drift.REFERENCE_PATH", str(tmp_path / "drift_reference.json")
    )
    from src.drift import capture_reference

    frame = pd.DataFrame(
        {
            "price_jpy": [1_000_000 + i * 10_000 for i in range(60)],
            "year": [2018 + (i % 8) for i in range(60)],
            "mileage_km": [30_000 + i * 500 for i in range(60)],
            "engine_cc": [1500] * 60,
        }
    )
    capture_reference(frame, model_version="test-model")
    return load_reference()


def stable_live_frame() -> pd.DataFrame:
    """Live frame drawn from the same distributions as the reference."""
    return pd.DataFrame(
        {
            "price_jpy": [1_050_000 + (i * 10_000) % 600_000 for i in range(60)],
            "year": [2018 + (i % 8) for i in range(60)],
            "mileage_km": [35_000 + (i * 500) % 30_000 for i in range(60)],
            "engine_cc": [1500] * 60,
        }
    )


def drifted_live_frame() -> pd.DataFrame:
    """Live frame whose prices jumped far above the reference band."""
    return pd.DataFrame(
        {
            "price_jpy": [3_500_000 + i * 20_000 for i in range(60)],
            "year": [2018 + (i % 8) for i in range(60)],
            "mileage_km": [35_000 + (i * 500) % 30_000 for i in range(60)],
            "engine_cc": [1500] * 60,
        }
    )


def test_stable_stream_stays_under_thresholds(reference):
    report = compute_psi(reference, stable_live_frame())
    assert report["enough_data"] is True
    assert report["retraining_recommended"] is False
    assert all(
        feature["psi"] is not None and feature["psi"] < 0.25
        for feature in report["features"].values()
    )


def test_price_shift_flags_retraining(reference):
    report = compute_psi(reference, drifted_live_frame())
    price_psi = report["features"]["price_jpy"]["psi"]
    assert price_psi is not None and price_psi > 0.25
    assert report["retraining_recommended"] is True
    assert report["worst_feature"] == "price_jpy"


def test_psi_detects_direction_agnostic_shift(reference):
    """Prices collapsing far below the band must drift too, not just rises."""
    frame = drifted_live_frame()
    frame["price_jpy"] = 200_000 - frame["price_jpy"] // 20
    report = compute_psi(reference, frame)
    assert report["features"]["price_jpy"]["psi"] > 0.25


def test_small_streams_do_not_trigger_retraining(reference):
    report = compute_psi(reference, drifted_live_frame().head(MIN_LIVE_ROWS - 1))
    assert report["enough_data"] is False
    assert report["retraining_recommended"] is False


def test_drift_report_without_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.drift.REFERENCE_PATH", str(tmp_path / "missing.json")
    )
    report = drift_report(stable_live_frame())
    assert report["status"] == "no_reference"
