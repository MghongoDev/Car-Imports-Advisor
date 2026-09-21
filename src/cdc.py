"""Change data capture via scheduled polling + diffing (Phase 2).

No webhooks exist for scraped marketplaces, so CDC here means: append-only
price history (SCD Type 2 style), content hashes per listing, and a
``price_events`` table recording detected changes. This is the honest,
appropriately-scoped pattern for a scraped data source.
"""

import logging
from typing import Optional

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from src.db_utils import ListingsHistory
from src.schemas import compute_content_hash

logger = logging.getLogger(__name__)


def _as_dict(listing_data):
    """Accept either a pydantic listing or a plain dict."""
    if hasattr(listing_data, "model_dump"):
        return listing_data.model_dump()
    return dict(listing_data)


def _latest_hash_for_listing(db: Session, listing_id: str) -> Optional[str]:
    """Return the most recent content hash stored for ``listing_id``."""
    stmt = (
        select(ListingsHistory.content_hash)
        .where(ListingsHistory.listing_id == listing_id)
        .order_by(desc(ListingsHistory.scraped_at))
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def _latest_price(db: Session, listing_id: str) -> Optional[int]:
    """Most recent stored price for ``listing_id`` (KES)."""
    stmt = (
        select(ListingsHistory.price_kes)
        .where(ListingsHistory.listing_id == listing_id)
        .order_by(desc(ListingsHistory.scraped_at))
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def classify_observation(db: Session, listing_data) -> str:
    """Classify a fresh observation as ``new``, ``changed``, or ``same``.

    ``new``    — first time we have ever seen this listing_id.
    ``changed``— seen before, but the content hash differs (price moved).
    ``same``   — seen before and nothing material changed.
    """
    data = _as_dict(listing_data)
    listing_id = data["listing_id"]
    new_hash = compute_content_hash(data)

    previous_hash = _latest_hash_for_listing(db, listing_id)
    if previous_hash is None:
        logger.info("First observation of listing %s — recording baseline.", listing_id)
        return "new"
    if previous_hash == new_hash:
        logger.debug("No change detected for listing %s.", listing_id)
        return "same"
    return "changed"


class PriceEventRecord:
    """In-memory change record produced by :func:`detect_changes`."""

    def __init__(self, listing_id, old_price_kes, new_price_kes, delta_kes, delta_pct):
        self.listing_id = listing_id
        self.old_price_kes = old_price_kes
        self.new_price_kes = new_price_kes
        self.delta_kes = delta_kes
        self.delta_pct = delta_pct


def detect_changes(db: Session, listing_data) -> Optional[PriceEventRecord]:
    """Compare a freshly scraped listing against stored history.

    Returns a change record when the content hash differs from the most
    recent stored hash, or ``None`` when nothing changed / no history exists.
    """
    data = _as_dict(listing_data)
    listing_id = data["listing_id"]
    new_hash = compute_content_hash(data)

    previous_hash = _latest_hash_for_listing(db, listing_id)
    if previous_hash is None:
        logger.info("First observation of listing %s — recording baseline.", listing_id)
        return None
    if previous_hash == new_hash:
        logger.debug("No change detected for listing %s.", listing_id)
        return None

    old_price = _latest_price(db, listing_id)
    new_price = data.get("price_kes")
    if old_price is None or new_price is None or old_price == new_price:
        return None

    delta_kes = new_price - old_price
    delta_pct = (delta_kes / old_price) * 100.0
    logger.info(
        "Price change for %s: %s -> %s KES (%.1f%%)",
        listing_id, old_price, new_price, delta_pct,
    )
    return PriceEventRecord(
        listing_id=listing_id,
        old_price_kes=old_price,
        new_price_kes=new_price,
        delta_kes=delta_kes,
        delta_pct=delta_pct,
    )
