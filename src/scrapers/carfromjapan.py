"""Car From Japan scraper (carfromjapan.com) — verified 2026-09.

Cards carry stable ``data-testid`` hooks (``car-card-title``,
``car-card-price``, ...) which are far more durable than CSS classes.
Prices are USD: a 'Car Price' plus a C&F 'Total'.
"""

import logging
import re
from typing import Optional

from src.scrapers.base import BaseScraper
from src.scrapers.parsing import normalize_make, parse_mileage_km
from src.schemas import CarListing

logger = logging.getLogger(__name__)

CFJ_LISTING_URL = "https://carfromjapan.com/cheap-used-toyota-for-sale"


def _testid(item, name: str):
    """Select a child element by its data-testid hook."""
    return item.select_one(f'[data-testid="{name}"]')


class CarFromJapanScraper(BaseScraper):
    """Scraper for carfromjapan.com listing pages."""

    source_name = "Car From Japan"
    base_url = "https://carfromjapan.com"

    def build_listing_url(self, page: int) -> str:
        """Return the listing page for ``page`` (1-indexed)."""
        if page <= 1:
            return CFJ_LISTING_URL
        return f"{CFJ_LISTING_URL}?page={page}"

    def listing_selector(self) -> str:
        """Verified: cards expose data-testid='car-card'."""
        return '[data-testid="car-card"]'

    def parse_listing(self, raw_item) -> Optional[CarListing]:
        """Parse one CFJ card into a validated CarListing."""
        title_el = _testid(raw_item, "car-card-title")
        title = title_el.get_text(" ", strip=True) if title_el else None
        if not title:
            return None

        price_el = _testid(raw_item, "car-card-total-price")
        price_usd = self._parse_usd(price_el.get_text() if price_el else None)
        if price_usd is None:
            return None

        mileage_el = _testid(raw_item, "car-card-mileage")
        engine_el = _testid(raw_item, "car-card-engine")
        transmission_el = _testid(raw_item, "car-card-transmission")
        reg_year_el = _testid(raw_item, "car-card-reg-year")
        link = raw_item.select_one("a[href]")

        year = self._parse_year(reg_year_el.get_text() if reg_year_el else title)
        href = link["href"] if link else CFJ_LISTING_URL

        warnings = []
        if year is None:
            warnings.append("year not found")

        return self.to_schema(
            listing_id=self._id_from_url(href) or title,
            url=self._absolute(href),
            make=self._derive_make(title),
            model=self._derive_model(title),
            year=year,
            mileage_km=parse_mileage_km(mileage_el.get_text() if mileage_el else None),
            engine_cc=self._parse_cc(engine_el.get_text() if engine_el else None),
            transmission=self._transmission(
                transmission_el.get_text() if transmission_el else None
            ),
            price_usd=price_usd,
            currency="USD",
            parse_warnings=warnings,
        )

    @staticmethod
    def _parse_usd(text: Optional[str]) -> Optional[int]:
        """Parse 'US$ 33,507' into an integer."""
        digits = re.sub(r"[^\d]", "", text or "")
        return int(digits) if digits else None

    @staticmethod
    def _parse_year(text: str) -> Optional[int]:
        match = re.search(r"(19|20)\d{2}", text or "")
        return int(match.group(0)) if match else None

    @staticmethod
    def _parse_cc(text: Optional[str]) -> Optional[int]:
        match = re.search(r"([\d,]+)\s*cc", text or "", flags=re.IGNORECASE)
        if not match:
            return None
        return int(match.group(1).replace(",", ""))

    @staticmethod
    def _transmission(text: Optional[str]) -> Optional[str]:
        upper = (text or "").upper()
        if "AT" in upper or "AUTOMATIC" in upper or "CVT" in upper:
            return "Automatic"
        if "MT" in upper or "MANUAL" in upper:
            return "Manual"
        return None

    @staticmethod
    def _id_from_url(url: str) -> Optional[str]:
        """CFJ detail URLs end in a dash token before .html."""
        match = re.search(r"-([A-Za-z0-9]{6,})\.html", url or "")
        return match.group(1) if match else None

    def _absolute(self, url: str) -> str:
        """Make root-relative hrefs absolute."""
        if url.startswith("http"):
            return url
        return self.base_url + (url if url.startswith("/") else "/" + url)

    @staticmethod
    def _derive_make(title: str) -> str:
        tokens = (title or "").split()
        raw = (
            tokens[1]
            if len(tokens) > 2 and re.match(r"^(19|20)\d{2}$", tokens[0])
            else (tokens[0] if tokens else "Unknown")
        )
        return normalize_make(raw)

    @staticmethod
    def _derive_model(title: str) -> str:
        tokens = (title or "").split()
        start = 2 if len(tokens) > 2 and re.match(r"^(19|20)\d{2}$", tokens[0]) else 1
        return " ".join(tokens[start:]) or "Unknown"
