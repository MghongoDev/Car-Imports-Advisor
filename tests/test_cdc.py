"""Tests for CDC diff logic (src/cdc.py) with a fixed before/after pair."""

import pytest

from src.cdc import classify_observation, compute_content_hash
from src.db_utils import append_history, init_db, make_session_factory


@pytest.fixture(name="db")
def fixture_db(tmp_path, monkeypatch):
    """A fresh SQLite database per test."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path/'test.db'}")
    init_db()
    return make_session_factory()


def listing_dict(price_kes=1_500_000, mileage_km=45_000):
    """A fixed listing observation, tweakable per test."""
    return {
        "listing_id": "jiji-001",
        "source": "Jiji",
        "url": "https://jiji.co.ke/jiji-001",
        "make": "Toyota",
        "model": "Axio",
        "year": 2020,
        "mileage_km": mileage_km,
        "price_kes": price_kes,
        "price_jpy": None,
        "title": "Toyota Axio 2020",
        "description": "Clean unit",
    }


def test_first_observation_is_new(db):
    with db() as session:
        assert classify_observation(session, listing_dict()) == "new"


def test_unchanged_listing_is_same(db):
    with db() as session:
        classify_observation(session, listing_dict())
        append_history(session, listing_dict())
        session.commit()
        assert classify_observation(session, listing_dict()) == "same"


def test_price_change_is_detected(db):
    with db() as session:
        classify_observation(session, listing_dict())
        append_history(session, listing_dict())
        session.commit()
        changed = listing_dict(price_kes=1_400_000)
        assert classify_observation(session, changed) == "changed"


def test_content_hash_changes_with_tracked_fields():
    base = compute_content_hash(listing_dict())
    assert base != compute_content_hash(listing_dict(price_kes=1_400_000))
    assert base != compute_content_hash(listing_dict(mileage_km=46_000))


def test_content_hash_stable_across_key_order():
    a = compute_content_hash({"price_kes": 1, "mileage_km": 2})
    b = compute_content_hash({"mileage_km": 2, "price_kes": 1})
    assert a == b
