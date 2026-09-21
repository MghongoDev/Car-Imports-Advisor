"""Japanese Car Trade scraper (japanesecartrade.com) — verified 2026-09.

Cards are anchor-wrapped blocks with an ``h3`` title and a ``div.price``
showing 'FOB : <s>12,830</s> <strong>11,550 USD</strong>'. We read the
Kenya-facing stock page (``/stock/kenya.html``) since the comparison targets
Kenyan buyers; prices are USD FOB.
"""

import logging
import re
from typing import Optional

from src.scrapers.base import BaseScraper
from src.scrapers.parsing import extract_make_model, normalize_make, parse_year
from src.schemas import CarListing

logger = logging.getLogger(__name__)

JCT_LISTING_URL = "https://www.japanesecartrade.com/stock/kenya.html"


class JapaneseCarTradeScraper(BaseScraper):
    """Scraper for japanesecartrade.com Kenya stock pages."""

    source_name = "Japanese Car Trade"
    base_url = "https://www.japanesecartrade.com"

    def build_listing_url(self, page: int) -> str:
        """Return the stock page for ``page`` (1-indexed)."""
        if page <= 1:
            return JCT_LISTING_URL
        return f"{JCT_LISTING_URL}?page={page}"

    def listing_selector(self) -> str:
        """Verified: cards are anchors containing an h3 title + div.price.
        (soupsieve lacks :has(), so we match title anchors and filter on
        price presence inside parse_listing.)"""
        return "a[href] h3"

    def parse_listing(self, raw_item) -> Optional[CarListing]:
        """Parse one JCT card (the h3 title) into a validated CarListing."""
        anchor = raw_item.find_parent("a") if raw_item.name != "a" else raw_item
        if anchor is None or anchor.select_one("div.price") is None:
            return None

        title = raw_item.get_text(strip=True)
        if not title:
            return None

        price_el = anchor.select_one("div.price strong")
        price_usd = self._parse_usd(price_el.get_text() if price_el else None)
        if price_usd is None:
            return None

        specs = anchor.get_text(" ", strip=True)
        year = parse_year(specs)
        engine_cc = self._parse_cc(specs)
        mileage_km = self._parse_mileage(specs)
        href = anchor.get("href") or JCT_LISTING_URL

        warnings = []
        if year is None:
            warnings.append("year not found")

        make, model = extract_make_model(title)
        return self.to_schema(
            listing_id=self._id_from_url(href) or title,
            url=self._absolute(href),
            make=normalize_make(make if make != "Unknown" else title.split()[0]),
            model=model if model != "Unknown" else " ".join(title.split()[1:]),
            year=year,
            mileage_km=mileage_km,
            engine_cc=engine_cc,
            price_usd=price_usd,
            currency="USD",
            parse_warnings=warnings,
        )

    def _absolute(self, url: str) -> str:
        """Make hrefs absolute."""
        if url.startswith("http"):
            return url
        return self.base_url + (url if url.startswith("/") else "/" + url)

    @staticmethod
    def _parse_usd(text: Optional[str]) -> Optional[int]:
        """Parse '11,550 USD' into an integer; 'ASK' yields None."""
        digits = re.sub(r"[^\d]", "", text or "")
        return int(digits) if digits else None

    @staticmethod
    def _parse_cc(text: str) -> Optional[int]:
        match = re.search(r"([\d,]+)\s*CC", text or "", flags=re.IGNORECASE)
        if not match:
            return None
        return int(match.group(1).replace(",", ""))

    @staticmethod
    def _parse_mileage(text: str) -> Optional[int]:
        match = re.search(r"([\d,]+)\s*KM", text or "", flags=re.IGNORECASE)
        if not match:
            return None
        value = int(match.group(1).replace(",", ""))
        return value if 0 < value <= 500_000 else None

    @staticmethod
    def _id_from_url(url: str) -> Optional[str]:
        """Detail URLs start with a numeric stock id: /13322852-japan-used-..."""
        match = re.search(r"/(\d{6,})-", url or "")
        return match.group(1) if match else None
