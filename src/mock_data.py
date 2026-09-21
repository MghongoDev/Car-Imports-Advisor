"""Deterministic demo data generator.

Mock data is only ever produced through an explicit, user-initiated action
(seed command or admin endpoint) — never as an automatic fallback inside a
scraper. See Phase 0 of the improvement guide.
"""

import random

import pandas as pd

from src.schemas import MAX_PRICE_JPY

SITE_SOURCES = [
    "SBT Japan",
    "BE FORWARD",
    "Car From Japan",
    "AAA Japan",
    "Japanese Car Trade",
]

# Correct make -> model relationships so demo rows stay plausible.
MAKE_MODEL_MAP = {
    "Toyota": ["Axio", "Vitz", "Fielder", "Wish", "FJ Cruiser", "RAV-4"],
    "Honda": ["Fit", "Vezel", "Grace", "CRV"],
    "Mazda": ["Demio", "CX-5", "Axela", "Atenza", "3", "CX-3"],
    "Nissan": ["Note", "X-Trail", "Skyline"],
    "Subaru": ["Impreza", "Forester", "Legacy", "Outback"],
    "Mitsubishi": ["Mirage", "Outlander"],
}

FUEL_TYPES = ["Petrol", "Diesel", "Hybrid"]
TRANSMISSIONS = ["Automatic", "Manual"]
BODY_TYPES = ["Sedan", "Hatchback", "SUV", "Wagon"]
ENGINE_CCS = [1300, 1500, 1800, 2000, 2400]


def generate_mock_data(n=500, source=None, jpy_kes_rate=0.95, seed=42):
    """Generate a demo DataFrame of car listings.

    This function exists only for demos and seeding an empty database. It is
    invoked exclusively from explicit entry points (``--seed-demo`` and
    ``POST /admin/seed-demo-data``), never automatically from a scraper.
    """
    rng = random.Random(seed)
    rows = []
    sources = [source] if source else SITE_SOURCES
    for i in range(n):
        year = rng.randint(2018, 2024)
        make = rng.choice(list(MAKE_MODEL_MAP))
        model = rng.choice(MAKE_MODEL_MAP[make])
        price_jpy = rng.randint(800_000, min(2_500_000, MAX_PRICE_JPY))
        age = 2024 - year
        mileage = max(1000, rng.randint(5000, 80_000) - age * 2000)
        rows.append(
            {
                "listing_id": f"demo-{i:05d}",
                "source": rng.choice(sources),
                "url": f"http://example.com/{make}/{model}",
                "make": make,
                "model": model,
                "year": year,
                "mileage_km": mileage,
                "engine_cc": rng.choice(ENGINE_CCS),
                "fuel_type": rng.choice(FUEL_TYPES),
                "transmission": rng.choice(TRANSMISSIONS),
                "body_type": rng.choice(BODY_TYPES),
                "price_jpy": price_jpy,
                "price_kes": int(round(price_jpy * jpy_kes_rate)),
                "exchange_rate": jpy_kes_rate,
                "parse_warnings": "",
            }
        )
    return pd.DataFrame(rows)
