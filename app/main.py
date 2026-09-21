"""FastAPI application entrypoint.

One service serves both the JSON API (``/docs`` for OpenAPI) and the
server-rendered Jinja2 frontend, deployed as a single Render web service.
"""

import logging
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import api, pages
from src.db_utils import init_db, run_migrations

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Car Imports Advisor",
    description=(
        "Compare importing cars from Japan vs buying locally in Kenya: "
        "scraped listings, CDC price history, ML price prediction, and "
        "KRA duty calculation."
    ),
    version="2.0.0",
)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(api.router, prefix="/api", tags=["api"])
app.include_router(pages.router, tags=["pages"])


@app.on_event("startup")
def on_startup():
    """Create tables and apply pending migrations on boot."""
    engine = init_db()
    run_migrations(engine)
    logger.info("Database ready (%s).", engine.url)


@app.get("/healthz", include_in_schema=False)
def healthz():
    """Plain liveness probe for Render."""
    return {"ok": True}
