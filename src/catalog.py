"""Car catalog queries powering the interactive compare-selection UI.

The dashboard lets a user pick make -> model -> year; the options for each
level come from listings actually present in the latest snapshot, so every
selectable combination is one the comparison engine can attempt.
"""

from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.comparison import ComparisonRow, min_importable_year, _match_for_local
from src.db_utils import ListingLatest

LOCAL_SOURCE = "Jiji"


def _local_rows(session: Session) -> List[Dict]:
    """Latest-snapshot rows for the local marketplace (KES priced)."""
    rows = session.execute(
        select(ListingLatest).where(ListingLatest.source == LOCAL_SOURCE)
    ).scalars().all()
    return [
        {k: v for k, v in row.__dict__.items() if not k.startswith("_")}
        for row in rows
    ]


def list_makes(session: Session) -> List[str]:
    """Distinct makes on the local market, alphabetically."""
    makes = {row.get("make") for row in _local_rows(session) if row.get("make")}
    return sorted(makes)


def list_models(session: Session, make: str) -> List[str]:
    """Distinct models for one make on the local market, alphabetically."""
    make_norm = (make or "").strip().lower()
    models = {
        row.get("model")
        for row in _local_rows(session)
        if (row.get("make") or "").strip().lower() == make_norm and row.get("model")
    }
    return sorted(models)


def list_years(session: Session, make: str, model: str) -> List[int]:
    """Distinct years for one make+model, newest first, import-eligible only."""
    make_norm = (make or "").strip().lower()
    model_norm = (model or "").strip().lower()
    year_floor = min_importable_year()
    years = {
        row["year"]
        for row in _local_rows(session)
        if (row.get("make") or "").strip().lower() == make_norm
        and (row.get("model") or "").strip().lower() == model_norm
        and row.get("year")
        and row["year"] >= year_floor
    }
    return sorted(years, reverse=True)


def comparison_for(
    session: Session,
    make: str,
    model: str,
    year: Optional[int] = None,
    *,
    usd_kes: Optional[float] = None,
    jpy_kes: Optional[float] = None,
) -> Optional[ComparisonRow]:
    """Build the local-vs-import comparison for one selected car.

    ``year`` is optional; without it the newest eligible year for the
    make+model is used. Returns None when the selection has no local
    listing, no eligible exporter counterpart, or no comparable pair.
    """
    make_norm = (make or "").strip().lower()
    model_norm = (model or "").strip().lower()
    candidates_local = [
        row for row in _local_rows(session)
        if (row.get("make") or "").strip().lower() == make_norm
        and (row.get("model") or "").strip().lower() == model_norm
        and row.get("price_kes")
    ]
    if not candidates_local:
        return None

    if year is None:
        years = [row["year"] for row in candidates_local if row.get("year")]
        year = max(years) if years else None
    local_row = next(
        (row for row in candidates_local if row.get("year") == year),
        None,
    ) or candidates_local[0]

    return _match_for_local(session, local_row, usd_kes=usd_kes, jpy_kes=jpy_kes)
