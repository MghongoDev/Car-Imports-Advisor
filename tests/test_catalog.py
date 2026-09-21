"""Tests for the compare-dashboard catalog layer (src/catalog.py)."""

import pytest

from src.catalog import (
    comparison_for,
    list_makes,
    list_models,
    list_years,
)
from src.comparison import min_importable_year
from src.db_utils import (
    append_history,
    init_db,
    make_session_factory,
    upsert_latest_listing,
)


@pytest.fixture(name="db")
def fixture_db(tmp_path, monkeypatch):
    """A fresh SQLite database per test."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path/'catalog.db'}")
    init_db()
    return make_session_factory()


def local_row(**overrides):
    """A Jiji (local, KES) listing dict, inside the import window."""
    base = {
        "listing_id": "loc-9",
        "source": "Jiji",
        "url": "https://jiji.co.ke/loc-9",
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
    """An SBT (exporter, USD) listing dict, inside the import window."""
    base = {
        "listing_id": "for-9",
        "source": "SBT Japan",
        "url": "https://www.sbtjapan.com/used-cars/toyota/fielder/AR9",
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


def test_catalog_cascades_make_model_year(db):
    """Selects expose only makes/models/years that exist locally."""
    with db() as session:
        seed(
            session,
            local_row(),
            local_row(listing_id="loc-2", model="Vitz", year=2021),
            foreign_row(),
        )
        assert list_makes(session) == ["Toyota"]
        assert list_models(session, "Toyota") == ["Fielder", "Vitz"]
        assert list_models(session, "Mazda") == []
        assert list_years(session, "Toyota", "Fielder") == [2019]
        assert list_years(session, "Toyota", "Vitz") == [2021]


def test_catalog_years_respect_import_floor(db):
    """Pre-2018 local rows are offered for browsing but not for import years.

    The year selects feed the import comparison, so anything older than
    the KEBS/BVRS 8-year floor is excluded there.
    """
    with db() as session:
        seed(session, local_row(year=2011, listing_id="loc-old"))
        years = list_years(session, "Toyota", "Fielder")
        assert all(y >= min_importable_year() for y in years)
        assert 2011 not in years


def test_comparison_for_returns_matched_pair(db):
    with db() as session:
        seed(session, local_row(), foreign_row())
        row = comparison_for(session, "Toyota", "Fielder", year=2019)
    assert row is not None
    assert row.local_price_kes == 1_250_000
    assert row.import_source == "SBT Japan"


def test_comparison_for_defaults_to_newest_year(db):
    """Without an explicit year, the newest eligible year is compared."""
    with db() as session:
        seed(
            session,
            local_row(year=2019, listing_id="loc-a"),
            local_row(year=2021, listing_id="loc-b", price_kes=1_600_000),
            foreign_row(year=2021),
        )
        row = comparison_for(session, "Toyota", "Fielder")
    assert row is not None
    assert row.year == 2021


def test_comparison_for_missing_selection_is_none(db):
    with db() as session:
        seed(session, local_row())
        assert comparison_for(session, "Mazda", "Demio") is None
        assert comparison_for(session, "Toyota", "Vitz") is None
