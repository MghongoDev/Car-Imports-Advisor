"""AAA Japan scraper (aaajapan.com) — verified 2026-09.

Stock cards expose schema.org microdata (``itemprop=name/price``) with JPY
prices that use dots as thousands separators ('290.000 JPY'). robots.txt
disallows detail pages and dealer stock — we only read the public
/cars-available gallery.
"""

import logging
import re
from typing import Optional

from src.scrapers.base import BaseScraper
from src.scrapers.parsing import extract_make_model, normalize_make
from src.schemas import CarListing

logger = logging.getLogger(__name__)

AAA_LISTING_URL = "https://aaajapan.com/cars-available"


class AaaJapanScraper(BaseScraper):
    """Scraper for aaajapan.com cars-available gallery."""

    source_name = "AAA Japan"
    base_url = "https://aaajapan.com"

    def build_listing_url(self, page: int) -> str:
        """Return the listing page for ``page`` (1-indexed)."""
        if page <= 1:
            return AAA_LISTING_URL
        return f"{AAA_LISTING_URL}?page={page}"

    def listing_selector(self) -> str:
        """Verified: offers are microdata items with itemprop='itemOffered'."""
        return '[itemprop="itemOffered"]'

    def parse_listing(self, raw_item) -> Optional[CarListing]:
        """Parse one AAA Japan microdata card into a validated CarListing."""
        name_meta = raw_item.select_one('[itemprop="name"]')
        name = name_meta.get("content") if name_meta else None
        if not name:
            return None

        # The price lives in the sibling makesOffer block; search the card
        # itself first, then fall back to the surrounding offer scope.
        price = self._parse_price(raw_item)
        if price is None:
            return None

        fields = {}
        for label_el in raw_item.select(".descrStrStock"):
            label = label_el.get_text(strip=True).rstrip(":").lower()
            value_el = label_el.find_next_sibling("div")
            if value_el is not None:
                fields[label] = value_el.get_text(strip=True)

        year = self._parse_year(fields.get("year") or name)
        mileage_km = self._parse_mileage(fields.get("mileage (km)"))
        engine_cc = self._parse_cc(fields.get("engine"))
        link = raw_item.select_one("a[href]")

        warnings = []
        if year is None:
            warnings.append("year not found")

        href = link["href"] if link else AAA_LISTING_URL
        brand_text = f"{fields.get('brand', '')} {fields.get('model', '')} {name}"
        make, model = extract_make_model(brand_text)
        return self.to_schema(
            listing_id=self._id_from_url(href) or name,
            url=self._absolute(href),
            make=normalize_make(make if make != "Unknown" else fields.get("brand", "Unknown")),
            model=model if model != "Unknown" else (fields.get("model") or "Unknown"),
            year=year,
            mileage_km=mileage_km,
            engine_cc=engine_cc,
            transmission=self._transmission(fields.get("transmission")),
            fuel_type=self._fuel(fields.get("fuel")),
            price_jpy=price,
            currency="JPY",
            parse_warnings=warnings,
        )

    def _parse_price(self, item) -> Optional[int]:
        """Read the JPY price from microdata ('290.000 JPY' -> 290000)."""
        scope = item
        parent = item.parent
        while parent is not None and scope.select_one('[itemprop="price"]') is None:
            scope = parent
            parent = parent.parent
            if scope.name in ("body", "html"):
                return None
        price_el = scope.select_one('[itemprop="price"]')
        if price_el is None:
            return None
        raw = price_el.get("content") or price_el.get_text()
        digits = re.sub(r"[^\d]", "", (raw or "").split()[0] if raw else "")
        return int(digits) if digits else None

    def _absolute(self, url: str) -> str:
        """Make root-relative hrefs absolute."""
        if url.startswith("http"):
            return url
        return self.base_url + (url if url.startswith("/") else "/" + url)

    @staticmethod
    def _id_from_url(url: str) -> Optional[str]:
        """Detail URLs look like /cars-available/toyota-voxy-zrr75-0074613-..."""
        match = re.search(r"-(\d{5,})-", url or "")
        return match.group(1) if match else None

    @staticmethod
    def _parse_year(text: str) -> Optional[int]:
        match = re.search(r"(19|20)\d{2}", text or "")
        return int(match.group(0)) if match else None

    @staticmethod
    def _parse_mileage(text: Optional[str]) -> Optional[int]:
        """AAA formats mileage with dot thousands ('189.000')."""
        if not text:
            return None
        digits = re.sub(r"[^\d]", "", text)
        value = int(digits) if digits else 0
        return value if 0 < value <= 500_000 else None

    @staticmethod
    def _parse_cc(text: Optional[str]) -> Optional[int]:
        match = re.search(r"(\d{3,4})", text or "")
        if not match:
            return None
        value = int(match.group(1))
        return value if 100 <= value <= 10_000 else None

    @staticmethod
    def _transmission(text: Optional[str]) -> Optional[str]:
        upper = (text or "").upper()
        if "AT" in upper or "AUTOMATIC" in upper or "CVT" in upper:
            return "Automatic"
        if "MT" in upper or "MANUAL" in upper:
            return "Manual"
        return None

    @staticmethod
    def _fuel(text: Optional[str]) -> Optional[str]:
        lowered = (text or "").lower()
        if "gasoline" in lowered or "petrol" in lowered:
            return "Petrol"
        if "diesel" in lowered:
            return "Diesel"
        if "hybrid" in lowered:
            return "Hybrid"
        return None
