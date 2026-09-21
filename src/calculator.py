"""Kenya import cost calculator (KRA duties and levies).

All rates are explicit, cited module constants so reviewers can trace every
number. Update ``RATES_LAST_REVIEWED`` whenever the rates are re-checked
against the linked sources.
"""

from typing import Any, Dict, Optional

from src.fx import DEFAULT_JPY_KES_RATE, fetch_exchange_rate, jpy_to_kes

# --- Cited rate constants ---------------------------------------------------
# Sources (checked 2026-09):
#   * Import Duty 25%: EAC Common External Tariff (KRA customs tariff, motor
#     cars HS 8703) — https://www.kra.go.ke/customs/import-duty
#   * Excise Duty: Excise Duty Act 2013 & KRA notices — 20% (<=1500cc),
#     30% (1501-2500cc), 35% (>2500cc) on (CIF + Import Duty).
#   * VAT 16%: VAT Act 2013 on (CIF + Import Duty + Excise Duty).
#   * IDF 3.5% of CIF: KRA Import Declaration Fee.
#   * RDL 2% of CIF: Railway Development Levy (Railways Act 2012).
RATES_LAST_REVIEWED = "2026-09"
IMPORT_DUTY_RATE = 0.25
VAT_RATE = 0.16
IDF_RATE = 0.035
RDL_RATE = 0.02
INSURANCE_RATE = 0.015  # 1.5% of FOB, standard KRA valuation practice

# Excise bands by engine capacity (cc).
EXCISE_BANDS = ((1500, 0.20), (2500, 0.30))
EXCISE_DEFAULT_RATE = 0.35

# Fixed on-the-ground estimates (KES).
PORT_CHARGES_KES = 50_000
CLEARING_AGENT_KES = 35_000
REGISTRATION_KES = 15_000

DEFAULT_FREIGHT_JPY = 250_000


def excise_rate_for(engine_cc: int) -> float:
    """Return the excise duty rate for an engine capacity in cc."""
    for upper, rate in EXCISE_BANDS:
        if engine_cc <= upper:
            return rate
    return EXCISE_DEFAULT_RATE


def calculate_from_cif(cif_kes: int, engine_cc: int) -> Dict[str, Any]:
    """Compute KRA duties from a CIF value in KES.

    Used both by :func:`calculate_import_cost` (JPY input path) and by the
    comparison layer (USD/JPY exporter listings already in KES).
    """
    import_duty_kes = int(round(cif_kes * IMPORT_DUTY_RATE))
    excise_rate = excise_rate_for(engine_cc)
    excise_duty_kes = int(round((cif_kes + import_duty_kes) * excise_rate))
    vat_kes = int(round((cif_kes + import_duty_kes + excise_duty_kes) * VAT_RATE))
    idf_kes = int(round(cif_kes * IDF_RATE))
    rdl_kes = int(round(cif_kes * RDL_RATE))
    total_taxes_kes = import_duty_kes + excise_duty_kes + vat_kes + idf_kes + rdl_kes
    return {
        "import_duty_kes": import_duty_kes,
        "excise_duty_kes": excise_duty_kes,
        "excise_rate": excise_rate,
        "vat_kes": vat_kes,
        "idf_kes": idf_kes,
        "rdl_kes": rdl_kes,
        "total_taxes_kes": total_taxes_kes,
    }


def calculate_import_cost(
    price_jpy: int,
    engine_cc: int,
    freight_jpy: int = DEFAULT_FREIGHT_JPY,
    use_live_fx: bool = False,
    fx_rate: Optional[float] = None,
) -> Dict[str, Any]:
    """Compute the full KES landed-cost breakdown for one car.

    Args:
        price_jpy: FOB price in JPY (e.g. a model prediction).
        engine_cc: Engine displacement in cc (selects the excise band).
        freight_jpy: Estimated shipping cost to Mombasa in JPY.
        use_live_fx: When True, fetch a live JPY->KES rate (falls back to
            the documented constant, logged as an estimate).
        fx_rate: Explicitly provided JPY->KES rate; overrides live fetching.

    Returns:
        Dict with snake_case keys (API-friendly) including every tax line,
        fixed charges, ``total_landed_cost_kes``, and the FX rate used.
    """
    rate = fx_rate
    if rate is not None:
        rate_is_fallback = False
    elif use_live_fx:
        rate, rate_is_fallback = fetch_exchange_rate()
    else:
        rate, rate_is_fallback = DEFAULT_JPY_KES_RATE, True

    fob_kes = jpy_to_kes(price_jpy, rate)
    freight_kes = jpy_to_kes(freight_jpy, rate)
    insurance_kes = int(round(fob_kes * INSURANCE_RATE))

    cif_kes = fob_kes + freight_kes + insurance_kes

    duties = calculate_from_cif(cif_kes, engine_cc)
    total_taxes_kes = duties["total_taxes_kes"]

    fixed_charges_kes = PORT_CHARGES_KES + CLEARING_AGENT_KES + REGISTRATION_KES
    total_landed_cost_kes = cif_kes + total_taxes_kes + fixed_charges_kes

    return {
        "fob_kes": fob_kes,
        "freight_kes": freight_kes,
        "insurance_kes": insurance_kes,
        "cif_kes": cif_kes,
        "import_duty_kes": duties["import_duty_kes"],
        "excise_duty_kes": duties["excise_duty_kes"],
        "excise_rate": duties["excise_rate"],
        "vat_kes": duties["vat_kes"],
        "idf_kes": duties["idf_kes"],
        "rdl_kes": duties["rdl_kes"],
        "port_charges_kes": PORT_CHARGES_KES,
        "clearing_agent_kes": CLEARING_AGENT_KES,
        "registration_kes": REGISTRATION_KES,
        "total_taxes_kes": duties["total_taxes_kes"],
        "fixed_charges_kes": fixed_charges_kes,
        "total_landed_cost_kes": total_landed_cost_kes,
        "exchange_rate": rate,
        "fx_is_estimate": rate_is_fallback,
        "rates_last_reviewed": RATES_LAST_REVIEWED,
    }
