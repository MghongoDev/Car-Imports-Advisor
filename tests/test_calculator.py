"""Tests for src/calculator.py — pure functions, highest-value coverage."""

from src.calculator import calculate_import_cost, excise_rate_for


def test_excise_bands():
    """Engine capacity selects the correct excise band."""
    assert excise_rate_for(1000) == 0.20
    assert excise_rate_for(1500) == 0.20
    assert excise_rate_for(1501) == 0.30
    assert excise_rate_for(2500) == 0.30
    assert excise_rate_for(2501) == 0.35
    assert excise_rate_for(3000) == 0.35


def test_breakdown_math_is_consistent():
    """Every line of the breakdown must sum to the total landed cost."""
    costs = calculate_import_cost(price_jpy=1_000_000, engine_cc=1500)
    assert costs["cif_kes"] > 0
    assert costs["total_taxes_kes"] == (
        costs["import_duty_kes"]
        + costs["excise_duty_kes"]
        + costs["vat_kes"]
        + costs["idf_kes"]
        + costs["rdl_kes"]
    )
    assert costs["total_landed_cost_kes"] == (
        costs["cif_kes"]
        + costs["total_taxes_kes"]
        + costs["fixed_charges_kes"]
    )
    # Total must strictly exceed CIF: all duty lines and fixed charges are positive.
    assert costs["total_landed_cost_kes"] > costs["cif_kes"]


def test_higher_engine_cc_costs_more():
    """A bigger engine in the same car must cost more in total."""
    small = calculate_import_cost(price_jpy=1_000_000, engine_cc=1500)
    big = calculate_import_cost(price_jpy=1_000_000, engine_cc=2000)
    assert big["excise_duty_kes"] > small["excise_duty_kes"]
    assert big["total_landed_cost_kes"] > small["total_landed_cost_kes"]


def test_explicit_fx_rate_is_not_flagged_as_estimate():
    """Caller-provided FX rates are treated as exact."""
    costs = calculate_import_cost(price_jpy=500_000, engine_cc=1300, fx_rate=1.0)
    assert costs["exchange_rate"] == 1.0
    assert costs["fx_is_estimate"] is False


def test_default_fx_is_flagged_as_estimate():
    """The documented fallback rate must be flagged, never passed off as live."""
    costs = calculate_import_cost(price_jpy=500_000, engine_cc=1300)
    assert costs["fx_is_estimate"] is True
    assert costs["rates_last_reviewed"]
