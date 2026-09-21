"""Tests for the scraper parsing helpers and schema contract."""

from src.scrapers.parsing import (
    coerce_engine_cc,
    coerce_fuel_type,
    coerce_transmission,
    extract_make_model,
    parse_int_from_text,
    parse_mileage_km,
    parse_price_jpy,
    parse_year,
)


def test_extract_make_model_keeps_pair_consistent():
    assert extract_make_model("Toyota Axio 2019") == ("Toyota", "Axio")
    assert extract_make_model("HONDA VEZEL Hybrid") == ("Honda", "Vezel")
    # Model present without make: the make must follow the model, not "Unknown".
    assert extract_make_model("Demio 13C")[0] == "Mazda"


def test_unknown_title_returns_unknowns():
    # No year token to anchor on and no known make/model: both stay Unknown.
    assert extract_make_model("Random Text") == ("Unknown", "Unknown")


def test_unknown_make_with_year_takes_first_token():
    # Titles always carry a year; an unknown make falls back to token 0.
    assert extract_make_model("Random Unknown Car 2010") == ("Random", "Unknown Car")


def test_multi_word_makes_are_not_split():
    assert extract_make_model("Land Rover Discovery 2012 Brown")[0] == "Land Rover"
    assert extract_make_model("Mercedes-Benz E220 2016 Silver")[0] == "Mercedes Benz"


def test_parse_int_handles_commas_and_garbage():
    assert parse_int_from_text("KES 1,250,000") == 1250000
    assert parse_int_from_text("no digits") is None
    assert parse_int_from_text(None) is None


def test_parse_year_bounds():
    assert parse_year("2019 Toyota") == 2019
    assert parse_year("1886") is None
    assert parse_year("2099") is None


def test_parse_mileage_bounds():
    assert parse_mileage_km("60,000 km") == 60000
    assert parse_mileage_km("99999999") is None


def test_parse_price_jpy_bounds():
    assert parse_price_jpy("JPY 1,250,000") == 1250000
    assert parse_price_jpy("0") is None


def test_coercers_normalise_scraped_text():
    assert coerce_engine_cc("1496cc") == 1496
    assert coerce_fuel_type("Petrol + Hybrid") == "Hybrid"
    assert coerce_transmission("CVT Automatic") == "Automatic"
    assert coerce_transmission("5MT Manual") == "Manual"
    assert coerce_fuel_type(None) is None
