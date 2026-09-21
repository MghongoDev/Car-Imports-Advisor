"""Shared pydantic schemas and canonical mapping helpers for car listings.

These models are the single source of truth for the shape of a listing:
the scraper validates every row against them before storage, the storage
layer maps rows through :func:`listing_to_row`, and the FastAPI layer
reuses them as response schemas. The CDC content hash also lives here so
hashing is canonicalised through the exact same mapping used for storage.
"""

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator

# Fields whose change should trigger a new history row / price event.
TRACKED_FIELDS = (
    "price_kes", "price_jpy", "price_usd", "mileage_km", "title", "description",
)


MIN_YEAR = 1990
MAX_YEAR = 2027
MAX_MILEAGE_KM = 500_000
MIN_PRICE_KES = 1_000
MAX_PRICE_KES = 50_000_000
MAX_PRICE_JPY = 20_000_000
MAX_PRICE_USD = 200_000
KNOWN_CURRENCIES = frozenset({"JPY", "KES", "USD"})
KNOWN_SOURCES = frozenset(
    {
        "SBT Japan",
        "BE FORWARD",
        "Car From Japan",
        "AAA Japan",
        "Japanese Car Trade",
        "Jiji",
    }
)


class CarListing(BaseModel):
    """A single scraped car listing, validated before it reaches storage."""

    listing_id: str = Field(min_length=1, description="Stable source listing id or URL.")
    source: str = Field(description="Marketplace the listing came from.")
    url: str = Field(min_length=1)
    make: str = Field(min_length=1)
    model: str = Field(min_length=1)
    year: int = Field(ge=MIN_YEAR, le=MAX_YEAR)
    mileage_km: Optional[int] = Field(default=None, ge=0, le=MAX_MILEAGE_KM)
    engine_cc: Optional[int] = Field(default=None, ge=100, le=10_000)
    fuel_type: Optional[str] = None
    transmission: Optional[str] = None
    body_type: Optional[str] = None
    price_jpy: Optional[int] = Field(default=None, gt=0, le=MAX_PRICE_JPY)
    price_kes: Optional[int] = Field(default=None, gt=0, le=MAX_PRICE_KES)
    exchange_rate: Optional[float] = Field(default=None, gt=0)
    price_usd: Optional[int] = Field(default=None, gt=0, le=MAX_PRICE_USD)
    currency: Optional[str] = None
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    parse_warnings: list[str] = Field(default_factory=list)

    @field_validator("currency")
    @classmethod
    def currency_must_be_known(cls, value: Optional[str]) -> Optional[str]:
        """Restrict currency to the set the comparison layer understands."""
        if value is None:
            return None
        upper = value.strip().upper()
        if upper not in KNOWN_CURRENCIES:
            raise ValueError(f"unknown currency: {value!r}")
        return upper

    @field_validator("source")
    @classmethod
    def source_must_be_known(cls, value: str) -> str:
        """Reject unknown sources so bad scraper wiring fails loudly."""
        if value not in KNOWN_SOURCES:
            raise ValueError(f"unknown source: {value!r} (expected one of {sorted(KNOWN_SOURCES)})")
        return value

    @field_validator("make", "model", "fuel_type", "transmission", "body_type")
    @classmethod
    def strip_text_fields(cls, value: Optional[str]) -> Optional[str]:
        """Normalise whitespace-padded scraped strings."""
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class ScrapeResult(BaseModel):
    """Outcome of one scraper run against one source."""

    source: str
    requested_url: str
    listings: list[CarListing] = Field(default_factory=list)
    parse_warnings: list[str] = Field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        """True when the fetch succeeded and produced at least one listing."""
        return self.error is None and bool(self.listings)


class PriceEvent(BaseModel):
    """A detected price change for a listing between two scrapes."""

    listing_id: str
    source: str
    old_price_kes: Optional[int] = None
    new_price_kes: int
    delta_kes: int
    delta_pct: float
    detected_at: datetime = Field(default_factory=datetime.utcnow)


def listing_to_row(listing) -> Dict[str, Any]:
    """Map a CarListing (pydantic) or dict onto the canonical storage columns.

    Single canonical mapping used by both the storage layer and CDC hashing,
    so a stored hash and a freshly computed hash always cover the same
    fields. ``parse_warnings`` is normalised to a list of strings.
    """
    if hasattr(listing, "model_dump"):
        data = listing.model_dump()
    else:
        data = dict(listing)
    warnings = data.get("parse_warnings") or []
    if isinstance(warnings, str):
        warnings = [warnings] if warnings else []
    return {
        "listing_id": data.get("listing_id"),
        "source": data.get("source"),
        "url": data.get("url"),
        "make": data.get("make"),
        "model": data.get("model"),
        "year": data.get("year"),
        "mileage_km": data.get("mileage_km"),
        "engine_cc": data.get("engine_cc"),
        "fuel_type": data.get("fuel_type"),
        "transmission": data.get("transmission"),
        "body_type": data.get("body_type"),
        "price_jpy": data.get("price_jpy"),
        "price_kes": data.get("price_kes"),
        "exchange_rate": data.get("exchange_rate"),
        "price_usd": data.get("price_usd"),
        "currency": data.get("currency"),
        "content_hash": data.get("content_hash"),
        "parse_warnings": warnings,
        "scraped_at": data.get("scraped_at"),
    }


def compute_content_hash(listing_data) -> str:
    """Compute a stable sha256 over the CDC-tracked fields of a listing.

    Accepts anything :func:`listing_to_row` accepts; the payload is built
    from the canonical row mapping so storage and diffing always agree.
    """
    row = listing_to_row(listing_data)
    payload = {field: row.get(field) for field in TRACKED_FIELDS}
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
