"""Server-rendered HTML pages (Phase 4.2).

Jinja2 templates rendered by FastAPI, with HTMX handling form posts and
partial refreshes without a full page reload.
"""

import logging
import os
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.calculator import calculate_import_cost
from src.comparison import min_importable_year
from src.db_utils import (
    fetch_latest_listings,
    fetch_price_events,
    last_pipeline_run,
    make_session_factory,
)
from src.fx import DEFAULT_JPY_KES_RATE
from src.ml_model import load_model, model_exists, predict_price, served_model_version

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    """Home page: search/listings view plus pipeline health snapshot."""
    run = last_pipeline_run()
    listings = fetch_latest_listings(limit=25)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "listings": listings,
            "last_scrape": run.get("finished_at") if run else None,
            "scrape_status": run.get("status") if run else "never run — trigger one below",
            "model_version": served_model_version() or "not trained",
            "model_available": model_exists(),
        },
    )


@router.get("/price-movements", response_class=HTMLResponse)
def price_movements(request: Request):
    """CDC output page: recent price changes sorted by magnitude."""
    events = fetch_price_events(limit=50)
    events = sorted(events, key=lambda e: e.get("delta_pct") or 0.0, reverse=True)
    return templates.TemplateResponse(
        request,
        "price_movements.html",
        {"events": events},
    )


@router.get("/comparisons", response_class=HTMLResponse)
def comparisons(request: Request):
    """Core feature page: local prices vs estimated import landed cost."""
    from src.comparison import build_comparisons  # pylint: disable=import-outside-toplevel

    with make_session_factory()() as session:
        rows = build_comparisons(session, limit=50)
    return templates.TemplateResponse(
        request,
        "comparisons.html",
        {"rows": [row.as_dict() for row in rows]},
    )


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    request: Request,
    make: str = "",
    model: str = "",
    year: int = 0,
):
    """Interactive compare dashboard: pick make -> model -> year.

    The selects cascade from the live catalog (only combinations that
    actually exist in the latest scrape), and the result fragment shows the
    local vs import landed-cost comparison for the chosen car.
    """
    from src.catalog import list_makes, list_models, list_years  # pylint: disable=import-outside-toplevel

    with make_session_factory()() as session:
        makes = list_makes(session)
        models = list_models(session, make) if make else []
        years = list_years(session, make, model) if make and model else []
    return templates.TemplateResponse(
        request,
        "compare.html",
        {
            "makes": makes,
            "models": models,
            "years": years,
            "selected_make": make,
            "selected_model": model,
            "selected_year": year,
            "min_year": min_importable_year(),
        },
    )


@router.get("/dashboard/options", response_class=HTMLResponse)
def dashboard_options(request: Request, make: str = "", model: str = ""):
    """HTMX fragment: re-rendered model/year selects after a change."""
    from src.catalog import list_models, list_years  # pylint: disable=import-outside-toplevel

    with make_session_factory()() as session:
        models = list_models(session, make) if make else []
        years = list_years(session, make, model) if make and model else []
    return templates.TemplateResponse(
        request,
        "partials/compare_selects.html",
        {
            "models": models,
            "years": years,
            "selected_model": model if years else "",
            "selected_year": 0,
        },
    )


@router.get("/dashboard/result", response_class=HTMLResponse)
def dashboard_result(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    request: Request,
    make: str = "",
    model: str = "",
    year: int = 0,
):
    """HTMX fragment: the comparison card for the selected car."""
    from src.catalog import comparison_for  # pylint: disable=import-outside-toplevel

    if not make or not model:
        return templates.TemplateResponse(request, "partials/compare_result.html", {})
    with make_session_factory()() as session:
        row = comparison_for(session, make, model, year=year or None)
    context: Dict[str, Any] = {
        "result": row.as_dict() if row else None,
        "make": make,
        "model": model,
        "year": year,
    }
    return templates.TemplateResponse(request, "partials/compare_result.html", context)


@router.get("/predict-page", response_class=HTMLResponse)
def predict_page(request: Request):
    """Render the prediction + import-cost form (replaces the old sidebar)."""
    return templates.TemplateResponse(request, "predict.html", {})


@router.post("/predict-result", response_class=HTMLResponse)
def predict_result(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    request: Request,
    make: str = "Toyota",
    model: str = "Axio",
    year: int = 2020,
    mileage_km: int = 30000,
    engine_cc: int = 1500,
    fuel_type: str = "Petrol",
    transmission: str = "Automatic",
    body_type: str = "Sedan",
    local_price_kes: int = 0,
):
    """HTMX target: predict + calculate, returning an HTML fragment."""
    context: Dict[str, Any] = {}
    trained = load_model()
    if trained is None:
        context["error"] = "Model not trained yet — run the pipeline first."
        return templates.TemplateResponse(request, "partials/predict_result.html", context)

    spec = {
        "make": make,
        "model": model,
        "year": year,
        "mileage_km": mileage_km,
        "engine_cc": engine_cc,
        "fuel_type": fuel_type,
        "transmission": transmission,
        "body_type": body_type,
    }
    predicted_jpy = predict_price(trained, spec)
    costs = calculate_import_cost(price_jpy=predicted_jpy, engine_cc=engine_cc)
    total_landed = costs["total_landed_cost_kes"]
    context.update(
        {
            "spec": spec,
            "predicted_jpy": predicted_jpy,
            "predicted_kes": int(predicted_jpy * DEFAULT_JPY_KES_RATE),
            "costs": costs,
            "local_price_kes": local_price_kes,
            "difference_kes": (local_price_kes - total_landed) if local_price_kes else None,
        }
    )
    return templates.TemplateResponse(request, "partials/predict_result.html", context)
