"""Local-vs-import price comparison (the project's core question).

For each car available BOTH locally (Jiji, KES) and from Japan-side
exporters (SBT, BE FORWARD, CFJ, JCT in USD; AAA Japan in JPY), estimate the
full landed cost of the Japan unit and compare it with the local asking
price.

Matching is deliberately conservative: same normalised make, same year, and
similar engine size when both sides know it. Unmatched local listings are
skipped rather than force-matched, so the comparison page only shows
trustworthy pairs.

Kenya's import rule (KEBS/BVRS pre-export verification): vehicles more than
8 years old from year of manufacture may not be imported, so exporter
listings older than that floor never enter a comparison.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.calculator import (
    CLEARING_AGENT_KES,
    INSURANCE_RATE,
    PORT_CHARGES_KES,
    REGISTRATION_KES,
    calculate_from_cif,
)
from src.db_utils import ListingLatest
from src.fx import fetch_exchange_rate

logger = logging.getLogger(__name__)

# Fallback USD->JPY used to normalise AAA Japan's JPY prices against the
# USD-quoted exporters when computing per-car freight estimates.
DEFAULT_USD_JPY_RATE = 150.0

# Kenya-bound Roro freight per car from Japan, USD. Documented estimate:
# 2026 quotes commonly land in the 1,200-2,000 USD band depending on vessel
# and season; we take a mid-band figure and flag it as an estimate.
ESTIMATED_FREIGHT_USD = 1_600

# KEBS/BVRS: vehicles aged over 8 years (from year of manufacture) may not
# be imported into Kenya. 8 years back from 2026 means 2018-and-newer stock.
MIN_IMPORTABLE_AGE_YEARS = 8


def min_importable_year(today: Optional[date] = None) -> int:
    """Oldest year-of-manufacture still legal to import into Kenya.

    Computed from the wall clock so the floor rolls forward each year
    (2026 -> 2018 floor, 2027 -> 2019 floor, and so on).
    """
    today = today or date.today()
    return today.year - MIN_IMPORTABLE_AGE_YEARS


@dataclass
class ComparisonRow:  # pylint: disable=too-many-instance-attributes
    """One local-vs-import comparison for a matched car."""

    make: str
    model: str
    year: int
    engine_cc: Optional[int]
    local_price_kes: int
    local_source: str
    local_url: str
    import_source: str
    import_url: str
    import_price_kes: int  # exporter price converted to KES (pre-duty)
    freight_kes: int
    cif_kes: int
    duties: Dict = field(default_factory=dict)
    fixed_charges_kes: int = PORT_CHARGES_KES + CLEARING_AGENT_KES + REGISTRATION_KES
    total_landed_cost_kes: int = 0
    difference_kes: int = 0  # local - landed; positive means importing saves
    difference_pct: float = 0.0

    def as_dict(self) -> Dict:
        """JSON-safe dict for the API layer."""
        return {
            "make": self.make,
            "model": self.model,
            "year": self.year,
            "engine_cc": self.engine_cc,
            "local_price_kes": self.local_price_kes,
            "local_source": self.local_source,
            "local_url": self.local_url,
            "import_source": self.import_source,
            "import_url": self.import_url,
            "import_price_kes": self.import_price_kes,
            "freight_kes": self.freight_kes,
            "cif_kes": self.cif_kes,
            "total_taxes_kes": self.duties.get("total_taxes_kes", 0),
            "fixed_charges_kes": self.fixed_charges_kes,
            "total_landed_cost_kes": self.total_landed_cost_kes,
            "difference_kes": self.difference_kes,
            "difference_pct": round(self.difference_pct, 1),
        }


def to_kes(listing_row: Dict, usd_kes: float, jpy_kes: float) -> Optional[int]:
    """Convert a listing's price to KES using its recorded currency."""
    currency = (listing_row.get("currency") or "").upper()
    price_usd = listing_row.get("price_usd")
    price_jpy = listing_row.get("price_jpy")
    price_kes = listing_row.get("price_kes")
    if currency == "KES" or price_kes:
        return int(price_kes) if price_kes else None
    if currency == "USD" or price_usd:
        return int(round(price_usd * usd_kes)) if price_usd else None
    if currency == "JPY" or price_jpy:
        return int(round(price_jpy * jpy_kes)) if price_jpy else None
    return None


def freight_kes_for(engine_cc: Optional[int], usd_kes: float) -> int:
    """Estimate Japan->Mombasa freight in KES (flagged as estimate upstream).

    Banded by engine size purely as a rough proxy for vessel space; the
    default band covers the vast majority of Kenyan imports.
    """
    usd = ESTIMATED_FREIGHT_USD
    if engine_cc is not None and engine_cc > 3000:
        usd += 200
    return int(round(usd * usd_kes))


def landed_cost_kes(
    exporter_price_kes: int,
    engine_cc: Optional[int],
) -> Dict:
    """Full landed-cost estimate from an exporter price (already in KES)."""
    insurance_kes = int(round(exporter_price_kes * INSURANCE_RATE))
    cif_kes = exporter_price_kes + insurance_kes
    duties = calculate_from_cif(cif_kes, engine_cc or 1500)
    fixed = PORT_CHARGES_KES + CLEARING_AGENT_KES + REGISTRATION_KES
    total = cif_kes + duties["total_taxes_kes"] + fixed
    return {
        "insurance_kes": insurance_kes,
        "cif_kes": cif_kes,
        "duties": duties,
        "fixed_charges_kes": fixed,
        "total_landed_cost_kes": total,
    }


# Filler tokens that carry no model identity when matching across sites
# ('Corolla Fielder 1.5G' vs 'Fielder' must still match).
_MODEL_STOP_TOKENS = frozenset(
    {"4wd", "2wd", "awd", "rhd", "fwd", "petrol", "diesel", "hybrid"}
)


def model_tokens(model: str) -> set:
    """Significant lowercased tokens of a model string."""
    return {
        token.lower().strip(".-")
        for token in (model or "").split()
        if token.lower().strip(".-") and token.lower() not in _MODEL_STOP_TOKENS
    }


def models_compatible(local_tokens: set, foreign_tokens: set) -> bool:
    """Whether two model token sets plausibly name the same car.

    Requires either two overlapping tokens ('Land Cruiser Prado' vs 'Land
    Cruiser Prado TRJ150') or one substantial token (>=4 chars, e.g.
    'Fielder', 'Voxy'). Single-letter trim grades ('X', 'G') never match
    alone — that would pair a Mark X with a Fielder X.
    """
    overlap = local_tokens & foreign_tokens
    if len(overlap) >= 2:
        return True
    return any(len(token) >= 4 for token in overlap)


def normalise_model_key(make: str, model: str) -> str:  # pylint: disable=unused-argument
    """Make key for the matching index (model matched via token overlap)."""
    return (make or "").lower()


def _year_of(row: Dict) -> Optional[int]:
    return row.get("year")


def build_comparisons(
    session: Session,
    usd_kes: Optional[float] = None,
    jpy_kes: Optional[float] = None,
    limit: int = 50,
) -> List[ComparisonRow]:
    """Compare local Jiji listings against Japan-side exporter listings.

    Both sides come from the latest-snapshot table; the caller is
    responsible for having run the pipeline recently.
    """
    rows = session.execute(select(ListingLatest)).scalars().all()
    listings = [
        {k: v for k, v in row.__dict__.items() if not k.startswith("_")}
        for row in rows
    ]
    local = [r for r in listings if r.get("source") == "Jiji" and r.get("price_kes")]

    # Batch path: fetch FX once for the whole batch (never per listing).
    if usd_kes is None or jpy_kes is None:
        live_usd, usd_is_fallback = fetch_exchange_rate("USD", "KES")
        live_jpy, jpy_is_fallback = fetch_exchange_rate("JPY", "KES")
        usd_kes = usd_kes if usd_kes is not None else live_usd
        jpy_kes = jpy_kes if jpy_kes is not None else live_jpy
        logger.info(
            "Comparison FX: USD/KES=%s (fallback=%s), JPY/KES=%s (fallback=%s)",
            usd_kes, usd_is_fallback, jpy_kes, jpy_is_fallback,
        )

    comparisons: List[ComparisonRow] = []
    for local_row in local:
        best = _match_for_local(session, local_row, usd_kes=usd_kes, jpy_kes=jpy_kes)
        if best is not None:
            comparisons.append(best)
        if len(comparisons) >= limit:
            break

    comparisons.sort(key=lambda c: c.difference_pct, reverse=True)
    return comparisons


def _exporter_candidates(session: Session, local_row: Dict) -> List[Dict]:
    """Exporter listings that could plausibly match one local listing.

    Same normalised make, meaningful model-token overlap, and inside
    Kenya's 8-year import window.
    """
    year_floor = min_importable_year()
    rows = session.execute(
        select(ListingLatest).where(ListingLatest.source != "Jiji")
    ).scalars().all()
    listings = [
        {k: v for k, v in row.__dict__.items() if not k.startswith("_")}
        for row in rows
    ]
    local_tokens = model_tokens(local_row.get("model", ""))
    local_key = normalise_model_key(local_row.get("make", ""), local_row.get("model", ""))
    candidates = []
    for row in listings:
        if not (row.get("price_usd") or row.get("price_jpy")):
            continue
        if (row.get("year") or 0) < year_floor:
            continue
        if normalise_model_key(row.get("make", ""), row.get("model", "")) != local_key:
            continue
        if not models_compatible(local_tokens, model_tokens(row.get("model", ""))):
            continue
        candidates.append(row)
    return candidates


def _match_for_local(
    session: Session,
    local_row: Dict,
    usd_kes: Optional[float] = None,
    jpy_kes: Optional[float] = None,
) -> Optional[ComparisonRow]:
    """Best local-vs-import comparison for one local listing, or None.

    Used both by the bulk comparison builder and by the interactive
    dashboard when a user picks a specific make/model/year.
    """
    if usd_kes is None or jpy_kes is None:
        live_usd, usd_is_fallback = fetch_exchange_rate("USD", "KES")
        live_jpy, jpy_is_fallback = fetch_exchange_rate("JPY", "KES")
        usd_kes = usd_kes if usd_kes is not None else live_usd
        jpy_kes = jpy_kes if jpy_kes is not None else live_jpy
        logger.info(
            "Comparison FX (single-selection path): USD/KES=%s (fallback=%s), "
            "JPY/KES=%s (fallback=%s)",
            usd_kes, usd_is_fallback, jpy_kes, jpy_is_fallback,
        )
    candidates = _exporter_candidates(session, local_row)
    return _best_candidate(local_row, candidates, usd_kes, jpy_kes)


def _best_candidate(
    local_row: Dict,
    candidates: List[Dict],
    usd_kes: float,
    jpy_kes: float,
) -> Optional[ComparisonRow]:
    """Pick the closest exporter listing for one local listing, if any.

    Ranking: exact year match first, then nearest year, then lowest landed
    cost. Returns None when no candidate is plausibly comparable.
    """
    if not candidates:
        return None

    def rank(row: Dict):
        year_gap = abs((_year_of(row) or 0) - (_year_of(local_row) or 0))
        landed = landed_cost_kes(
            to_kes(row, usd_kes, jpy_kes) or 0,
            row.get("engine_cc"),
        )
        return (year_gap, landed["total_landed_cost_kes"])

    candidates_sorted = sorted(candidates, key=rank)
    best = candidates_sorted[0]

    best_year_gap = abs((_year_of(best) or 0) - (_year_of(local_row) or 0))
    if best_year_gap > 1:
        return None  # different model years are not comparable enough

    engine_cc = best.get("engine_cc") or local_row.get("engine_cc")
    exporter_kes = to_kes(best, usd_kes, jpy_kes)
    if exporter_kes is None:
        return None
    if (best.get("year") or 0) < min_importable_year():
        return None  # older than Kenya's 8-year import limit

    freight = freight_kes_for(engine_cc, usd_kes)
    cost = landed_cost_kes(exporter_kes + freight, engine_cc)

    local_price = int(local_row["price_kes"])
    total = cost["total_landed_cost_kes"]
    difference = local_price - total
    return ComparisonRow(
        make=local_row.get("make", "Unknown"),
        model=local_row.get("model", "Unknown"),
        year=local_row.get("year") or 0,
        engine_cc=engine_cc,
        local_price_kes=local_price,
        local_source=local_row.get("source", "Jiji"),
        local_url=local_row.get("url", ""),
        import_source=best.get("source", ""),
        import_url=best.get("url", ""),
        import_price_kes=exporter_kes,
        freight_kes=freight,
        cif_kes=cost["cif_kes"],
        duties=cost["duties"],
        fixed_charges_kes=cost["fixed_charges_kes"],
        total_landed_cost_kes=total,
        difference_kes=difference,
        difference_pct=(difference / local_price) * 100.0 if local_price else 0.0,
    )
