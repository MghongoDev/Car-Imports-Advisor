"""SBT Japan scraper (sbtjapan.com) — verified against live HTML 2026-09.

Cards: ``div.card-product`` on ``/used-cars/search``. Prices are quoted in
USD (vehicle price plus a "Total Price" that includes shipping); the vehicle
price is stored in ``price_usd`` and the currency flagged accordingly.
"""

import logging
import re
from typing import Optional

from src.scrapers.base import BaseScraper
from src.scrapers.parsing import coerce_fuel_type, normalize_make, parse_mileage_km
from src.schemas import CarListing

logger = logging.getLogger(__name__)

SBT_SEARCH_URL = "https://www.sbtjapan.com/used-cars/search"


def _parse_year_month(title: str) -> Optional[int]:
    """SBT titles lead with 'YYYY/M MAKE MODEL' — take the year part."""
    match = re.match(r"\s*(19|20)\d{2}", title or "")
    return int(match.group(0)) if match else None


class SbtJapanScraper(BaseScraper):
    """Scraper for sbtjapan.com used-car search results."""

    source_name = "SBT Japan"
    base_url = "https://www.sbtjapan.com"

    def build_listing_url(self, page: int) -> str:
        """Return the SBT search page for ``page`` (1-indexed)."""
        if page <= 1:
            return SBT_SEARCH_URL
        return f"{SBT_SEARCH_URL}?page={page}"

    def listing_selector(self) -> str:
        """Verified: each stock card is a ``div.card-product``."""
        return "div.card-product"

    def parse_listing(self, raw_item) -> Optional[CarListing]:
        """Parse one SBT card into a validated CarListing."""
        title_el = raw_item.select_one(".card-product__product")
        title = title_el.get_text(strip=True) if title_el else None
        if not title:
            return None

        price_el = raw_item.select_one(
            ".card-product__vehicle-price .card-product__price"
        )
        price_usd = self._parse_usd(price_el)
        if price_usd is None:
            return None

        year = _parse_year_month(title)
        stock_el = raw_item.select_one(".card-product__stock-value")
        stock_id = stock_el.get_text(strip=True) if stock_el else None
        link = raw_item.select_one("a.card-product__wrap[href]")

        status = {}
        for item in raw_item.select(".card-product__status"):
            key = next(
                (c for c in item.get("class", []) if c.startswith("-") and c != "-search_result"),
                None,
            )
            if key:
                status[key] = item.get_text(strip=True)

        engine_cc = self._parse_cc(status.get("-engine-capacity", ""))
        warnings = []
        if year is None:
            warnings.append("year not found")

        href = link["href"] if link else SBT_SEARCH_URL
        return self.to_schema(
            listing_id=stock_id or href.rstrip("/").rsplit("/", 1)[-1],
            url=self.base_url + href,
            make=self._derive_make(title, href),
            model=self._derive_model(title),
            year=year,
            mileage_km=parse_mileage_km(status.get("-mileage", "")),
            engine_cc=engine_cc,
            fuel_type=coerce_fuel_type(status.get("-fuel-type", "")),
            transmission=self._transmission(status.get("-transmission", "")),
            price_usd=price_usd,
            currency="USD",
            parse_warnings=warnings,
        )

    @staticmethod
    def _parse_usd(element) -> Optional[int]:
        """Parse an SBT price element ('<span>USD</span><span>5,790</span>')."""
        if element is None:
            return None
        digits = re.sub(r"[^\d]", "", element.get_text())
        return int(digits) if digits else None

    @staticmethod
    def _parse_cc(text: str) -> Optional[int]:
        """Parse '2,362cc' into an integer displacement."""
        match = re.search(r"([\d,]+)\s*cc", text or "", flags=re.IGNORECASE)
        if not match:
            return None
        return int(match.group(1).replace(",", ""))

    @staticmethod
    def _transmission(text: str) -> Optional[str]:
        """Map SBT's 'AT'/'MT'/'CAT' markers to normalised labels."""
        upper = (text or "").upper()
        if "AT" in upper:
            return "Automatic"
        if "MT" in upper:
            return "Manual"
        return None

    @staticmethod
    def _derive_make(title, href) -> str:
        """Prefer the make segment of the detail URL ('/used-cars/toyota/...')."""
        match = re.search(r"/used-cars/([a-z0-9-]+)/", href or "", flags=re.IGNORECASE)
        if match:
            return normalize_make(match.group(1).replace("-", " "))
        return normalize_make((title or "").split()[-1]) if title else "Unknown"

    @staticmethod
    def _derive_model(title) -> str:
        """Model is everything after the 'YYYY/M MAKE' prefix.

        SBT titles lead with a combined year/month token ('2007/2 TOYOTA
        RAV4 G'), so drop that token plus the make token.
        """
        tokens = (title or "").split()
        if len(tokens) >= 3 and re.match(r"^(19|20)\d{2}", tokens[0]):
            return " ".join(tokens[2:])
        return " ".join(tokens[1:]) if len(tokens) > 1 else "Unknown"
