"""Shared feature engineering for training and drift monitoring.

Kept in its own module so ``ml_model`` (training) and ``drift``
(monitoring) can both depend on it without importing each other.
"""

import os

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "models")
os.makedirs(MODEL_DIR, exist_ok=True)

FEATURE_COLUMNS = [
    "make", "model", "car_age", "mileage_per_year", "engine_cc",
    "fuel_type", "transmission", "body_type",
]
CATEGORICAL_FEATURES = ["make", "model", "fuel_type", "transmission", "body_type"]
NUMERICAL_FEATURES = ["car_age", "mileage_per_year", "engine_cc"]
CURRENT_YEAR = 2024


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add car_age and mileage_per_year; keep original columns intact."""
    out = df.copy()
    if "car_age" not in out.columns:
        out["car_age"] = CURRENT_YEAR - out["year"].astype(int)
    if "mileage_per_year" not in out.columns:
        age_safe = out["car_age"].clip(lower=1)
        mileage = out["mileage_km"].fillna(0).astype(float)
        out["mileage_per_year"] = mileage / age_safe
    return out
