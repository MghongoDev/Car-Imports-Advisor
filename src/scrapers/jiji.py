"""Jiji.co.ke scraper — the primary local marketplace source (Phase 1).

Jiji renders listing cards server-side, so a plain requests+BeautifulSoup
scraper works. Every parsed row goes through ``to_schema`` validation; rows
missing required fields are dropped and recorded as parse warnings instead
of being silently invented.
"""

import logging
from typing import Optional

from src.scrapers.base import BaseScraper
from src.scrapers.parsing import (
    coerce_body_type,
    coerce_engine_cc,
    coerce_fuel_type,
    coerce_transmission,
    derive_listing_id,
    extract_make_model,
    parse_mileage_km,
    parse_price_kes,
    parse_year,
)
from src.schemas import CarListing

logger = logging.getLogger(__name__)

JIJI_BASE_URL = "https://jiji.co.ke"
JIJI_CARS_PATH = "/cars"
ATTR_SELECTOR = ".b-list-advert-base__item-attr"


class JijiScraper(BaseScraper):
    """Scraper for jiji.co.ke car listings."""

    source_name = "Jiji"
    base_url = JIJI_BASE_URL

    def build_listing_url(self, page: int) -> str:
        """Return the Jiji cars listing page for ``page`` (1-indexed)."""
        if page <= 1:
            return JIJI_BASE_URL + JIJI_CARS_PATH
        return f"{JIJI_BASE_URL}{JIJI_CARS_PATH}?page={page}"

    def listing_selector(self) -> str:
        """Verified against live HTML (2026-09): gallery cards use
        ``b-adverts-gallery-listing__item``; each contains one anchor with
        the ``qa-advert-list-item`` marker class."""
        return "div.b-adverts-gallery-listing__item"

    def parse_listing(self, raw_item) -> Optional[CarListing]:
        """Parse one Jiji listing card into a validated CarListing.

        Card anatomy (verified on live pages):
        - title: ``.qa-advert-title`` ("Toyota Fielder 2017 Silver")
        - price: ``.qa-advert-price`` ("KSh 1,650,000")
        - attrs: ``.b-list-advert-base__item-attr`` (condition, transmission)
        - link:  anchor href (``/karen/cars/<slug>-<id>.html?...``)
        Mileage and engine size are NOT on gallery cards — they stay None
        and are recorded as parse warnings rather than guessed.
        """
        title = self._extract_title(raw_item)
        if not title:
            return None

        make, model = extract_make_model(title)
        price_kes = parse_price_kes(self._extract_price_text(raw_item))
        if price_kes is None:
            return None

        year = parse_year(title)
        attrs = [el.get_text(strip=True) for el in raw_item.select(ATTR_SELECTOR)]
        attrs_text = " ".join(attrs)

        warnings = []
        if year is None:
            warnings.append("year not found")
        if "mileage" not in attrs_text.lower():
            warnings.append("mileage not shown on gallery card")
        mileage_km = parse_mileage_km(
            next((a for a in attrs if "km" in a.lower()), None)
        )
        engine_cc = coerce_engine_cc(title)
        if engine_cc is None:
            warnings.append("engine_cc not shown on gallery card")

        url = self._extract_url(raw_item) or JIJI_CARS_PATH
        return self.to_schema(
            listing_id=derive_listing_id(url),
            url=self._absolute_url(url),
            make=make,
            model=model,
            year=year,
            mileage_km=mileage_km,
            engine_cc=engine_cc,
            fuel_type=coerce_fuel_type(title),
            transmission=coerce_transmission(attrs_text),
            body_type=coerce_body_type(title),
            price_jpy=None,
            price_kes=price_kes,
            parse_warnings=warnings,
        )

    @staticmethod
    def _absolute_url(url: str) -> str:
        """Jiji card hrefs are root-relative; make them absolute."""
        if url.startswith("http"):
            return url
        if url.startswith("/"):
            return JIJI_BASE_URL + url
        return f"{JIJI_BASE_URL}/{url}"

    @staticmethod
    def _extract_title(item):
        element = item.select_one(".qa-advert-title")
        return element.get_text(strip=True) if element else None

    @staticmethod
    def _extract_price_text(item):
        element = item.select_one(".qa-advert-price")
        return element.get_text(strip=True) if element else None

    @staticmethod
    def _extract_url(item):
        anchor = item.select_one("a[href]")
        return anchor["href"] if anchor else None
