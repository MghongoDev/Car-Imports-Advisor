#!/usr/bin/env python
"""Pipeline entrypoint: scrape -> validate -> store -> detect changes.

Both the GitHub Actions cron job and local developers call this script the
same way, so scheduled and on-demand runs share identical logic.

Usage:
    python scripts/run_pipeline.py                # run scrape + ingest
    python scripts/run_pipeline.py --seed-demo    # seed demo data instead
    python scripts/run_pipeline.py --pages 3      # scrape 3 listing pages
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

# Allow direct invocation (python scripts/run_pipeline.py) by putting the
# project root on sys.path before importing the src package.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _utcnow():
    """Naive UTC timestamp (keeps SQLite storage simple)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

from src.cdc import classify_observation, detect_changes  # noqa: E402  pylint: disable=wrong-import-position
from src.db_utils import (  # noqa: E402  pylint: disable=wrong-import-position
    PipelineRun,
    append_history,
    init_db,
    make_session_factory,
    record_price_event,
    run_migrations,
    store_dataframe,
    upsert_latest_listing,
)
from src.fx import fetch_exchange_rate  # noqa: E402  pylint: disable=wrong-import-position
from src.mock_data import generate_mock_data  # noqa: E402  pylint: disable=wrong-import-position
from src.scrapers import (  # noqa: E402  pylint: disable=wrong-import-position
    AaaJapanScraper,
    BeforwardScraper,
    CarFromJapanScraper,
    JapaneseCarTradeScraper,
    JijiScraper,
    SbtJapanScraper,
)
from src.scrapers.base import FetchBlockedError  # noqa: E402  pylint: disable=wrong-import-position

logger = logging.getLogger("pipeline")


def configure_logging():
    """Structured console logging for scheduled and local runs."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def run_scrape(pages=1):
    """Scrape all configured sources and ingest results. Returns run stats."""
    engine = init_db()
    run_migrations(engine)
    started = _utcnow()
    run = PipelineRun(status="running", started_at=started)
    factory = make_session_factory()

    rate, is_fallback = fetch_exchange_rate()
    if is_fallback:
        logger.warning("Using fallback FX rate %s — results flagged as estimates.", rate)

    stats = {"scraped": 0, "new": 0, "changes": 0, "warnings": 0}
    # Jiji supplies local KES prices; the rest supply Japan-side export
    # prices (USD, or JPY for AAA Japan) that the comparison layer matches
    # against them.
    scrapers = [
        JijiScraper(),
        SbtJapanScraper(),
        BeforwardScraper(),
        CarFromJapanScraper(),
        AaaJapanScraper(),
        JapaneseCarTradeScraper(),
]
    with factory() as session:
        session.add(run)
        session.commit()
        for scraper in scrapers:
            try:
                result = scraper.fetch_listings(pages=pages)
            except FetchBlockedError as exc:
                logger.error("%s blocked; skipping: %s", scraper.source_name, exc)
                continue
            except Exception as exc:  # noqa: BLE001 - one source must not kill the run
                logger.error("%s failed: %s", scraper.source_name, exc)
                continue

            stats["scraped"] += len(result.listings)
            stats["warnings"] += len(result.parse_warnings)
            stats["warnings"] += sum(len(item.parse_warnings) for item in result.listings)
            for listing in result.listings:
                data = listing.model_dump()
                classification = classify_observation(session, data)
                # Detect the change BEFORE appending this observation,
                # otherwise the just-inserted row becomes the "previous"
                # hash and no price event can ever fire.
                change = (
                    detect_changes(session, data)
                    if classification == "changed"
                    else None
                )
                append_history(session, data)
                upsert_latest_listing(session, data)
                if classification == "new":
                    stats["new"] += 1
                elif change is not None:
                    stats["changes"] += 1
                    record_price_event(session, change)
        run.finished_at = _utcnow()
        run.status = "success"
        run.listings_scraped = stats["scraped"]
        run.listings_new = stats["new"]
        run.price_changes = stats["changes"]
        run.parse_warnings = stats["warnings"]
        session.commit()
    logger.info("Run complete: %s", stats)
    return stats


def seed_demo(n=500):
    """Explicitly seed the database with clearly-labelled demo data."""
    engine = init_db()
    run_migrations(engine)
    df = generate_mock_data(n=n)
    store_dataframe(df, table_name="demo_listings")
    logger.info("Seeded %d demo rows into demo_listings.", len(df))


def main():
    """CLI entrypoint."""
    configure_logging()
    parser = argparse.ArgumentParser(description="Car imports data pipeline")
    parser.add_argument("--pages", type=int, default=1, help="listing pages per source")
    parser.add_argument("--seed-demo", action="store_true", help="seed demo data and exit")
    parser.add_argument("--demo-rows", type=int, default=500, help="rows when seeding demo data")
    args = parser.parse_args()

    if args.seed_demo:
        seed_demo(args.demo_rows)
        return 0
    run_scrape(pages=args.pages)
    return 0


if __name__ == "__main__":
    sys.exit(main())
