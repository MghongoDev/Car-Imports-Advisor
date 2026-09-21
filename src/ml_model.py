"""Model training, comparison, and versioned persistence (Phase 5).

Improvements over the original single-RandomForest approach:
- Feature engineering: car age and mileage-per-year instead of raw year/mileage.
- Model comparison: median baseline vs RandomForest vs GradientBoosting,
  evaluated on the same split, with a comparison table returned per run.
- Versioned artifacts: models are saved as ``car_price_model_<version>.pkl``
  plus a ``latest`` pointer, so any run can be rolled back.
- Run tracking: params/metrics/artifact per run in ``models/runs.json``
  (a lightweight stand-in for MLflow's local file store).
"""

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import joblib
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder
from sklearn.dummy import DummyRegressor
from sklearn.pipeline import Pipeline

from src.features import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    MODEL_DIR,
    NUMERICAL_FEATURES,
    engineer_features,
)

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
RUNS_LOG_PATH = os.path.join(MODEL_DIR, "runs.json")
LATEST_POINTER_PATH = os.path.join(MODEL_DIR, "latest_model.txt")


def model_exists() -> bool:
    """True when a trained model artifact is available on disk."""
    return os.path.exists(latest_model_path())


def latest_model_path() -> str:
    """Path of the currently-served model artifact."""
    pointer = os.path.join(MODEL_DIR, "latest_model.txt")
    if os.path.exists(pointer):
        with open(pointer, "r", encoding="utf-8") as handle:
            version = handle.read().strip()
        if version:
            candidate = os.path.join(MODEL_DIR, f"car_price_model_{version}.pkl")
            if os.path.exists(candidate):
                return candidate
    return os.path.join(MODEL_DIR, "car_price_model.pkl")


def served_model_version() -> Optional[str]:
    """Human-readable version string of the served model (for /health)."""
    path = latest_model_path()
    name = os.path.basename(path)
    if name.startswith("car_price_model_") and name.endswith(".pkl"):
        return name[len("car_price_model_"):-len(".pkl")]
    return "legacy" if os.path.exists(path) else None


def load_model():
    """Load the currently-served model, or None when nothing is trained."""
    try:
        return joblib.load(latest_model_path())
    except (FileNotFoundError, EOFError, OSError):
        return None


def load_runs_log() -> List[Dict[str, Any]]:
    """Read the JSON runs log (empty list when missing/corrupt)."""
    if not os.path.exists(RUNS_LOG_PATH):
        return []
    try:
        with open(RUNS_LOG_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (ValueError, OSError):
        return []


def save_runs_log(runs: List[Dict[str, Any]]) -> None:
    """Persist the JSON runs log."""
    with open(RUNS_LOG_PATH, "w", encoding="utf-8") as handle:
        json.dump(runs, handle, indent=2, default=str)


def build_pipeline(regressor) -> Pipeline:
    """Assemble preprocessing + regressor into one pipeline."""
    preprocessor = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
            ("num", "passthrough", NUMERICAL_FEATURES),
        ]
    )
    return Pipeline(steps=[("preprocessor", preprocessor), ("regressor", regressor)])


def evaluate_candidate(name: str, pipeline: Pipeline, splits: Tuple) -> Dict[str, Any]:
    """Fit one candidate on the train split and score it on the test split."""
    x_train, x_test, y_train, y_test = splits
    start = time.time()
    pipeline.fit(x_train, y_train)
    elapsed = time.time() - start
    preds = pipeline.predict(x_test)
    return {
        "model_name": name,
        "mae_jpy": float(mean_absolute_error(y_test, preds)),
        "r2": float(r2_score(y_test, preds)),
        "fit_seconds": round(elapsed, 2),
        "pipeline": pipeline,
    }


def compare_models(df: pd.DataFrame) -> Tuple[List[Dict[str, Any]], Any, Tuple]:
    """Train and compare baseline, RandomForest, and GradientBoosting."""
    data = engineer_features(df)
    x = data[FEATURE_COLUMNS]
    y = data["price_jpy"]
    splits = train_test_split(x, y, test_size=0.2, random_state=42)

    candidates = {
        "median_baseline": build_pipeline(DummyRegressor(strategy="median")),
        "random_forest": build_pipeline(
            RandomForestRegressor(n_estimators=150, random_state=42, n_jobs=-1)
        ),
        "gradient_boosting": build_pipeline(
            GradientBoostingRegressor(random_state=42)
        ),
    }
    results = [
        evaluate_candidate(name, pipe, splits) for name, pipe in candidates.items()
    ]
    return results, candidates, splits


def train_and_save_model(df: pd.DataFrame) -> Dict[str, Any]:
    """Compare candidates, persist the winner, and log the run.

    Returns a summary dict: comparison table, chosen model, version string,
    and the artifact path. Raises ValueError on degenerate training data.
    """
    if df is None or len(df) < 10 or "price_jpy" not in df.columns:
        raise ValueError(
            "Need at least 10 rows with a price_jpy column to train the model."
        )

    results, candidates, _ = compare_models(df)
    best = min(results, key=lambda item: item["mae_jpy"])
    version = time.strftime("%Y%m%d_%H%M%S")

    artifact_path = os.path.join(MODEL_DIR, f"car_price_model_{version}.pkl")
    joblib.dump(candidates[best["model_name"]], artifact_path)
    with open(LATEST_POINTER_PATH, "w", encoding="utf-8") as handle:
        handle.write(version)

    run_record = {
        "version": version,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "rows": int(len(df)),
        "comparison": [
            {k: v for k, v in item.items() if k != "pipeline"} for item in results
        ],
        "chosen": best["model_name"],
        "artifact": os.path.basename(artifact_path),
    }
    runs = load_runs_log()
    runs.append(run_record)
    save_runs_log(runs)

    # Capture the training distribution as the drift-monitoring reference.
    # Imported here: src.drift is a monitoring-layer module and must not be
    # a training-time import dependency chain from module load.
    from src.drift import capture_reference  # pylint: disable=import-outside-toplevel

    capture_reference(df, version)

    return {
        "version": version,
        "chosen": best["model_name"],
        "comparison": run_record["comparison"],
        "artifact": artifact_path,
    }


def predict_price(model, input_data: Dict[str, Any]) -> float:
    """Predict the JPY price for one car's spec dict (engineers features)."""
    frame = pd.DataFrame([input_data])
    frame = engineer_features(frame)
    prediction = model.predict(frame[FEATURE_COLUMNS])
    return float(prediction[0])
