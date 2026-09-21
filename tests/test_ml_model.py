"""Tests for src/ml_model.py: features, comparison, versioned artifacts."""

import os

import pytest

from src.ml_model import (
    engineer_features,
    served_model_version,
    train_and_save_model,
)
from src.mock_data import generate_mock_data


@pytest.fixture(name="model_env")
def fixture_model_env(tmp_path, monkeypatch):
    """Redirect model artifacts to a temp dir per test."""
    monkeypatch.setattr("src.ml_model.MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(
        "src.ml_model.LATEST_POINTER_PATH", os.path.join(str(tmp_path), "latest_model.txt")
    )
    monkeypatch.setattr(
        "src.ml_model.RUNS_LOG_PATH", os.path.join(str(tmp_path), "runs.json")
    )
    monkeypatch.setattr(
        "src.drift.REFERENCE_PATH", os.path.join(str(tmp_path), "drift_reference.json")
    )
    return tmp_path


def test_engineer_features_adds_age_and_mileage_per_year():
    import pandas as pd

    frame = pd.DataFrame(
        [{"year": 2020, "mileage_km": 40_000, "make": "Toyota", "model": "Axio"}]
    )
    out = engineer_features(frame)
    assert out["car_age"].iloc[0] == 4
    assert out["mileage_per_year"].iloc[0] == 10_000


def test_train_and_save_creates_versioned_artifact(model_env):
    df = generate_mock_data(n=120)
    summary = train_and_save_model(df)
    artifact = summary["artifact"]
    assert os.path.exists(artifact)
    assert os.path.basename(artifact).startswith("car_price_model_")
    assert served_model_version() == summary["version"]
    # Comparison table must contain all three candidates with metrics.
    names = {row["model_name"] for row in summary["comparison"]}
    assert names == {"median_baseline", "random_forest", "gradient_boosting"}
    assert all("mae_jpy" in row and "r2" in row for row in summary["comparison"])


def test_train_rejects_tiny_dataset():
    import pandas as pd

    with pytest.raises(ValueError):
        train_and_save_model(pd.DataFrame([{"price_jpy": 1}]))
