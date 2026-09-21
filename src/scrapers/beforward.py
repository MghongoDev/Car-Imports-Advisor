"""BE FORWARD scraper (beforward.jp) — verified against live HTML 2026-09.

Cards: ``div.stocklist-row`` on ``/stocklist/...``. Prices are quoted in USD
(``vehicle-price``); the spec table carries mileage, year and engine cc.
Note: BE FORWARD's robots.txt disallows /market_price/ — we only fetch
public stocklist pages.
"""

import logging
import re
from typing import Optional

from src.scrapers.base import BaseScraper
from src.scrapers.parsing import normalize_make, parse_mileage_km
from src.schemas import CarListing

logger = logging.getLogger(__name__)

BEF_STOCKLIST_URL = "https://www.beforward.jp/stocklist/"


def _spec_value(row, klass: str) -> Optional[str]:
    """Read one cell of BE FORWARD's basic-spec-row table."""
    cell = row.select_one(f".basic-spec-col.{klass} .val")
    return cell.get_text(strip=True) if cell else None


class BeforwardScraper(BaseScraper):
    """Scraper for beforward.jp stocklist pages."""

    source_name = "BE FORWARD"
    base_url = "https://www.beforward.jp"

    def build_listing_url(self, page: int) -> str:
        """Return the stocklist page for ``page`` (1-indexed)."""
        if page <= 1:
            return BEF_STOCKLIST_URL
        return f"{BEF_STOCKLIST_URL}?page={page}"

    def listing_selector(self) -> str:
        """Verified: stock rows are ``div.stocklist-row``."""
        return "div.stocklist-row"

    def parse_listing(self, raw_item) -> Optional[CarListing]:
        """Parse one BE FORWARD row into a validated CarListing."""
        title_el = raw_item.select_one(".make-model a")
        title = title_el.get_text(" ", strip=True) if title_el else None
        if not title:
            return None

        price_el = raw_item.select_one(".vehicle-price .price")
        price_usd = self._parse_usd(price_el.get_text() if price_el else None)
        if price_usd is None:
            return None

        link = raw_item.select_one("a.vehicle-url-link[href]")
        href = link["href"] if link else BEF_STOCKLIST_URL
        ref_el = raw_item.select_one(".veh-stock-no a span")
        ref_text = ref_el.get_text(" ", strip=True) if ref_el else ""
        ref_no = ref_text.replace("Ref No.", "").strip() or None

        year = self._parse_year(_spec_value(raw_item, "year") or title)
        mileage_km = parse_mileage_km(_spec_value(raw_item, "mileage"))
        engine_cc = self._parse_cc(_spec_value(raw_item, "engine"))

        warnings = []
        if year is None:
            warnings.append("year not found")

        return self.to_schema(
            listing_id=ref_no or href.rstrip("/").rsplit("/", 2)[-2],
            url=self.base_url + href,
            make=self._derive_make(href),
            model=self._derive_model(title),
            year=year,
            mileage_km=mileage_km,
            engine_cc=engine_cc,
            transmission=self._transmission(_spec_value(raw_item, "shift")),
            fuel_type=self._fuel(_spec_value(raw_item, "fuel")),
            price_usd=price_usd,
            currency="USD",
            parse_warnings=warnings,
        )

    @staticmethod
    def _parse_usd(text: Optional[str]) -> Optional[int]:
        """Parse '$16,720' into an integer."""
        digits = re.sub(r"[^\d]", "", text or "")
        return int(digits) if digits else None

    @staticmethod
    def _parse_year(text: str) -> Optional[int]:
        """Parse '2017/3' or a year embedded in a title."""
        match = re.search(r"(19|20)\d{2}", text or "")
        return int(match.group(0)) if match else None

    @staticmethod
    def _parse_cc(text: str) -> Optional[int]:
        """Parse '1,991cc' into an integer displacement."""
        match = re.search(r"([\d,]+)\s*cc", text or "", flags=re.IGNORECASE)
        if not match:
            return None
        return int(match.group(1).replace(",", ""))

    @staticmethod
    def _transmission(text: str) -> Optional[str]:
        """Map BE FORWARD's shift column (e.g. 'FA', 'MT') to a label."""
        upper = (text or "").upper()
        if not upper:
            return None
        if "F" in upper and "A" in upper or "AT" in upper or "CA" in upper:
            return "Automatic"
        if "MT" in upper or "M" == upper:
            return "Manual"
        return None

    @staticmethod
    def _fuel(text: str) -> Optional[str]:
        """Normalise the fuel column."""
        lowered = (text or "").lower()
        if "gasoline" in lowered or "petrol" in lowered:
            return "Petrol"
        if "diesel" in lowered:
            return "Diesel"
        if "hybrid" in lowered:
            return "Hybrid"
        return None

    @staticmethod
    def _derive_make(href: str) -> str:
        """Detail URLs look like /mercedes-benz/glc-class/ce876399/id/.../."""
        parts = [p for p in (href or "").split("/") if p]
        return normalize_make(parts[0].replace("-", " ")) if parts else "Unknown"

    @staticmethod
    def _derive_model(title: str) -> str:
        """Title is 'YYYY MAKE MODEL...' — drop year and make tokens."""
        tokens = (title or "").split()
        start = 0
        if tokens and re.match(r"^(19|20)\d{2}$", tokens[0]):
            start = 1
        if len(tokens) > start + 1:
            start += 1  # drop the make token
        return " ".join(tokens[start:]) or "Unknown"
