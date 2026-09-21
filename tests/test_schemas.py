"""Tests for src/schemas.py — the data-quality gate for scraped rows."""

import pytest
from pydantic import ValidationError

from src.schemas import CarListing


def make_valid_listing(**overrides):
    """A minimal valid listing with optional per-field overrides."""
    base = dict(
        listing_id="abc-123",
        source="Jiji",
        url="https://jiji.co.ke/abc-123",
        make="Toyota",
        model="Axio",
        year=2020,
        mileage_km=45_000,
        price_kes=1_500_000,
    )
    base.update(overrides)
    return base


def test_valid_listing_passes():
    listing = CarListing(**make_valid_listing())
    assert listing.make == "Toyota"
    assert listing.parse_warnings == []


def test_unknown_source_rejected():
    """A typo'd source string must fail loudly, not silently."""
    with pytest.raises(ValidationError):
        CarListing(**make_valid_listing(source="Not A Real Site"))


def test_year_out_of_range_rejected():
    with pytest.raises(ValidationError):
        CarListing(**make_valid_listing(year=1950))


def test_negative_price_rejected():
    with pytest.raises(ValidationError):
        CarListing(**make_valid_listing(price_kes=-5))


def test_unrealistic_mileage_rejected():
    with pytest.raises(ValidationError):
        CarListing(**make_valid_listing(mileage_km=10_000_000))


def test_text_fields_are_stripped():
    listing = CarListing(**make_valid_listing(make="  Toyota  ", fuel_type=" "))
    assert listing.make == "Toyota"
    assert listing.fuel_type is None  # whitespace-only becomes None


def test_missing_required_field_rejected():
    with pytest.raises(ValidationError):
        CarListing(**{k: v for k, v in make_valid_listing().items() if k != "make"})
