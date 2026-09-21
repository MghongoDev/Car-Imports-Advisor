"""JSON API endpoints (Phase 4.1).

All endpoints reuse the pydantic schemas and domain modules so the API is a
thin layer over the same code the pipeline and scrapers use. The interactive
dashboard drives the same endpoints via HTMX partials.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from src.calculator import calculate_import_cost
from src.db_utils import (
    fetch_latest_listings,
    fetch_listing_history,
    fetch_price_events,
    fetch_price_stream_frame,
    last_pipeline_run,
    load_dataframe,
    make_session_factory,
)
from src.drift import drift_report
from src.ml_model import load_model, model_exists, predict_price, served_model_version
from src.scrapers import JijiScraper

logger = logging.getLogger(__name__)

router = APIRouter()


class PredictRequest(BaseModel):
    """Car spec input for price prediction."""

    make: str = Field(min_length=1)
    model: str = Field(min_length=1)
    year: int = Field(ge=1990, le=2027)
    mileage_km: int = Field(ge=0)
    engine_cc: int = Field(ge=100, le=10000)
    fuel_type: str = "Petrol"
    transmission: str = "Automatic"
    body_type: str = "Sedan"


class PredictResponse(BaseModel):
    """Predicted JPY price plus the served model version."""

    predicted_price_jpy: float
    model_version: Optional[str] = None


class CalculateRequest(BaseModel):
    """Import-cost calculation input.

    ``price_jpy`` accepts floats so the output of ``/predict`` can be passed
    straight into ``/calculate`` without manual rounding.
    """

    price_jpy: float = Field(gt=0)
    engine_cc: int = Field(ge=100, le=10000)
    freight_jpy: float = Field(default=250000, ge=0)
    use_live_fx: bool = False


class SeedDemoRequest(BaseModel):
    """Optional body for the seed-demo admin endpoint."""

    rows: int = Field(default=500, ge=10, le=10000)


@router.get("/health")
def health() -> Dict[str, Any]:
    """Pipeline liveness: last run status, served model version, drift flag."""
    run = last_pipeline_run()
    drift = drift_report(fetch_price_stream_frame())
    return {
        "status": "ok",
        "last_scrape_at": run.get("finished_at") if run else None,
        "last_scrape_status": run.get("status") if run else "never_run",
        "model_version": served_model_version(),
        "model_available": model_exists(),
        "drift_status": drift.get("status"),
        "retraining_recommended": bool(drift.get("retraining_recommended")),
    }


@router.post("/predict", response_model=PredictResponse)
def predict(request: PredictRequest) -> PredictResponse:
    """Predict the FOB price in JPY for one car spec."""
    model = load_model()
    if model is None:
        raise HTTPException(status_code=503, detail="Model not trained yet.")
    prediction = predict_price(model, request.model_dump())
    return PredictResponse(
        predicted_price_jpy=prediction,
        model_version=served_model_version(),
    )


@router.post("/calculate")
def calculate(request: CalculateRequest) -> Dict[str, Any]:
    """Full KES landed-cost breakdown for a JPY price + engine size."""
    return calculate_import_cost(
        price_jpy=int(round(request.price_jpy)),
        engine_cc=request.engine_cc,
        freight_jpy=int(round(request.freight_jpy)),
        use_live_fx=request.use_live_fx,
    )


@router.get("/listings")
def listings(
    source: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Recent listings from the latest-snapshot table."""
    rows = fetch_latest_listings(limit=limit)
    if source:
        rows = [row for row in rows if row.get("source") == source]
    return rows


@router.get("/listings/{listing_id}/history")
def listing_history(listing_id: str) -> List[Dict[str, Any]]:
    """Full price/observation history for one listing."""
    history = fetch_listing_history(listing_id)
    if not history:
        raise HTTPException(status_code=404, detail="Listing not found.")
    return history


@router.get("/price-events")
def price_events(limit: int = 50) -> List[Dict[str, Any]]:
    """Recent price changes, largest moves first."""
    events = fetch_price_events(limit=limit)
    return sorted(events, key=lambda event: event.get("delta_pct") or 0.0, reverse=True)


@router.get("/comparisons")
def comparisons(limit: int = 50) -> List[Dict[str, Any]]:
    """Local (Jiji) vs import (Japan exporters) landed-cost comparisons.

    Matches scraped local listings with exporter listings of the same
    make/year, estimates the full landed cost of the Japan unit (exporter
    price + freight + KRA duties) and returns the difference in KES.
    """
    from src.comparison import build_comparisons  # pylint: disable=import-outside-toplevel

    with make_session_factory()() as session:
        rows = build_comparisons(session, limit=limit)
    return [row.as_dict() for row in rows]


@router.get("/drift")
def drift_status() -> Dict[str, Any]:
    """PSI drift report for the CDC price stream vs the training reference.

    Compares the newest observation per listing against the distribution
    captured at training time; ``retraining_recommended`` flips true when
    any monitored feature's PSI crosses the action threshold.
    """
    return drift_report(fetch_price_stream_frame())


@router.get("/catalog/makes")
def catalog_makes() -> List[str]:
    """Distinct makes on the local market, for the dashboard's first select."""
    from src.catalog import list_makes  # pylint: disable=import-outside-toplevel

    with make_session_factory()() as session:
        return list_makes(session)


@router.get("/catalog/models")
def catalog_models(make: str) -> List[str]:
    """Distinct models for one make on the local market."""
    from src.catalog import list_models  # pylint: disable=import-outside-toplevel

    with make_session_factory()() as session:
        return list_models(session, make)


@router.get("/catalog/years")
def catalog_years(make: str, model: str) -> List[int]:
    """Import-eligible years (>= 8-year floor) for one make+model, newest first."""
    from src.catalog import list_years  # pylint: disable=import-outside-toplevel

    with make_session_factory()() as session:
        return list_years(session, make, model)


@router.get("/comparison")
def comparison(make: str, model: str, year: Optional[int] = None) -> Dict[str, Any]:
    """Local-vs-import comparison for one user-selected car.

    Returns 404 when the selection has no local listing or no comparable,
    import-eligible exporter counterpart — never a force-matched guess.
    """
    from src.catalog import comparison_for  # pylint: disable=import-outside-toplevel

    with make_session_factory()() as session:
        row = comparison_for(session, make, model, year=year)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No comparable local/import pair found for {make} {model} "
                f"{year or ''}".strip() + "."
            ),
        )
    return row.as_dict()


@router.get("/demo-data")
def demo_data(limit: int = 20) -> List[Dict[str, Any]]:
    """Read rows from the seeded demo table (explicit demo endpoint)."""
    frame = load_dataframe(table_name="demo_listings")
    return frame.head(limit).to_dict(orient="records")


@router.post("/admin/trigger-scrape")
def trigger_scrape(background_tasks: BackgroundTasks, pages: int = 1) -> Dict[str, str]:
    """Kick off a scrape run in the background (live-demo helper)."""
    # Imported here so the API can start without the scripts package
    # present in slim deployments; pylint: disable=import-outside-toplevel
    from scripts.run_pipeline import run_scrape

    background_tasks.add_task(run_scrape, pages)
    return {"status": "scrape scheduled", "pages": str(pages)}


@router.post("/admin/seed-demo-data")
def seed_demo_data(request: SeedDemoRequest) -> Dict[str, str]:
    """Explicitly seed clearly-labelled demo data (never automatic)."""
    # Imported here to avoid importing scripts at module load.
    from scripts.run_pipeline import seed_demo  # pylint: disable=import-outside-toplevel

    seed_demo(request.rows)
    return {"status": "demo data seeded", "rows": str(request.rows)}


@router.get("/robots-status")
def robots_status() -> Dict[str, Any]:
    """Report the current robots.txt verdict for the configured source."""
    scraper = JijiScraper()
    allowed = scraper.check_robots_txt()
    return {"source": scraper.source_name, "robots_allows": allowed}
