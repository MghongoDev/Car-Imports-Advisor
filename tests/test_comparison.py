"""Tests for the local-vs-import comparison layer."""

import pytest

from src.comparison import (
    build_comparisons,
    freight_kes_for,
    landed_cost_kes,
    model_tokens,
    models_compatible,
    normalise_model_key,
    to_kes,
)
from src.db_utils import (
    append_history,
    init_db,
    make_session_factory,
    upsert_latest_listing,
)


@pytest.fixture(name="db")
def fixture_db(tmp_path, monkeypatch):
    """A fresh SQLite database per test."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path/'cmp.db'}")
    init_db()
    return make_session_factory()


def local_row(**overrides):
    """A Jiji (local, KES) listing dict (2019: import-eligible era)."""
    base = {
        "listing_id": "loc-1",
        "source": "Jiji",
        "url": "https://jiji.co.ke/loc-1",
        "make": "Toyota",
        "model": "Fielder",
        "year": 2019,
        "engine_cc": 1500,
        "price_kes": 1_250_000,
        "currency": "KES",
    }
    base.update(overrides)
    return base


def foreign_row(**overrides):
    """An SBT (exporter, USD) listing dict (2019: import-eligible era)."""
    base = {
        "listing_id": "for-1",
        "source": "SBT Japan",
        "url": "https://www.sbtjapan.com/used-cars/toyota/fielder/AR1",
        "make": "Toyota",
        "model": "FIELDER X",
        "year": 2019,
        "engine_cc": 1500,
        "price_usd": 6_500,
        "currency": "USD",
    }
    base.update(overrides)
    return base


def seed(session, *rows):
    """Store listings into both snapshot tables."""
    for row in rows:
        append_history(session, row)
        upsert_latest_listing(session, row)
    session.commit()


def test_to_kes_converts_each_currency():
    usd_row = {"price_usd": 100, "currency": "USD", "price_jpy": None, "price_kes": None}
    jpy_row = {"price_usd": None, "currency": "JPY", "price_jpy": 1000, "price_kes": None}
    kes_row = {"price_usd": None, "currency": None, "price_jpy": None, "price_kes": 7000}
    assert to_kes(usd_row, usd_kes=129.0, jpy_kes=1.0) == 12_900
    assert to_kes(jpy_row, usd_kes=129.0, jpy_kes=0.95) == 950
    assert to_kes(kes_row, usd_kes=129.0, jpy_kes=1.0) == 7_000
    assert to_kes({"price_usd": None, "currency": None, "price_jpy": None, "price_kes": None}, 129.0, 1.0) is None


def test_landed_cost_exceeds_exporter_price():
    cost = landed_cost_kes(1_000_000, 1500)
    # 25% duty + 20% excise + 16% VAT + 3.5% IDF + 2% RDL on top of CIF.
    assert cost["total_landed_cost_kes"] == 1_921_925
    assert cost["duties"]["import_duty_kes"] > 0
    assert cost["cif_kes"] > 1_000_000  # includes insurance


def test_freight_scales_with_engine_size():
    small = freight_kes_for(1500, 129.0)
    big = freight_kes_for(3500, 129.0)
    assert big > small


def test_models_compatible_precision():
    assert models_compatible({"fielder", "x"}, {"fielder", "x", "1.5g"})  # 2-token overlap
    assert models_compatible({"fielder"}, {"corolla", "fielder"})  # substantial token
    assert not models_compatible({"x"}, {"x"})  # single-letter trims never match
    assert not models_compatible({"voxy"}, {"voxel"})


def test_normalise_model_key_is_make_only():
    assert normalise_model_key("Toyota", "Fielder") == "toyota"
    assert normalise_model_key("TOYOTA", "X") == normalise_model_key("toyota", "Y")


def test_end_to_end_comparison(db):
    """Local Fielder vs SBT Fielder produces one sane comparison row."""
    with db() as session:
        seed(session, local_row(), foreign_row())
        rows = build_comparisons(session, usd_kes=129.0, jpy_kes=0.95)
    assert len(rows) == 1
    row = rows[0]
    assert row.local_price_kes == 1_250_000
    # USD 6,500 * 129 = 838,500 + freight ~206k + duties -> ~2.4M landed
    assert 2_000_000 < row.total_landed_cost_kes < 3_000_000
    assert row.difference_kes == row.local_price_kes - row.total_landed_cost_kes
    assert row.difference_pct < 0  # importing costs more at these fixture prices
    assert row.import_source == "SBT Japan"


def test_year_gate_blocks_distant_matches(db):
    """A 2007 local car must not match a 2023 exporter unit."""
    with db() as session:
        seed(session, local_row(year=2007), foreign_row(year=2023))
        rows = build_comparisons(session, usd_kes=129.0, jpy_kes=0.95)
    assert rows == []


def test_jpy_exporter_price_is_compared(db):
    """AAA Japan's JPY prices must enter comparisons, not be ignored."""
    aaa_row = foreign_row(
        listing_id="aaa-1",
        source="AAA Japan",
        url="https://aaajapan.com/cars-available/x",
        model="VOXY",
        price_usd=None,
        price_jpy=900_000,
        currency="JPY",
        year=2019,
    )
    with db() as session:
        seed(session, local_row(model="Voxy"), aaa_row)
        rows = build_comparisons(session, usd_kes=129.0, jpy_kes=0.95)
    assert len(rows) == 1
    assert rows[0].import_price_kes == 855_000  # 900k * 0.95


def test_import_year_floor_excludes_old_export_listings(db):
    """Kenya's 8-year rule: pre-floor exporter units never get matched.

    A cheaper 2011 exporter unit would win on price, but it is not legal
    to import, so the comparison must come back empty rather than show an
    impossible deal.
    """
    with db() as session:
        seed(session, local_row(year=2019), foreign_row(year=2011))
        rows = build_comparisons(session, usd_kes=129.0, jpy_kes=0.95)
    assert rows == []


def test_min_importable_year_rolls_forward():
    """Floor = current year minus 8 (2026 -> 2018, 2027 -> 2019, ...)."""
    from datetime import date

    from src.comparison import min_importable_year

    assert min_importable_year(date(2026, 6, 15)) == 2018
    assert min_importable_year(date(2027, 1, 1)) == 2019
    assert min_importable_year(date(2020, 12, 31)) == 2012


def test_unmatched_local_listings_are_skipped(db):
    """Conservative matching: no forced pairs when the make is absent."""
    with db() as session:
        seed(session, local_row(make="Ferrari", model="458"))
        rows = build_comparisons(session, usd_kes=129.0, jpy_kes=0.95)
    assert rows == []
