"""Base scraper interface with resilient fetching and politeness controls.

Phase 1 of the improvement guide: resilient HTTP layer (tenacity backoff on
transient failures, no retry when blocked), polite delays, structured
logging, and a ``to_schema`` contract that validates every row with pydantic
before it can reach storage.
"""

import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

import requests
from bs4 import BeautifulSoup
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from src.scrapers.parsing import derive_listing_id
from src.schemas import CarListing, ScrapeResult

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT_SECONDS = 15
POLITE_DELAY_SECONDS = 1.5
BLOCKED_STATUS_CODES = frozenset({401, 403, 429})


class FetchBlockedError(RuntimeError):
    """Raised when the target site blocks us (403/401/429) — do not retry."""


def _retryable(exc: BaseException) -> bool:
    """Retry transient network failures but never deliberate blocks."""
    if isinstance(exc, FetchBlockedError):
        return False
    return isinstance(exc, (requests.ConnectionError, requests.Timeout))


class BaseScraper(ABC):
    """Common HTTP + parsing contract for all marketplace scrapers."""

    source_name: str = "Unknown"
    base_url: str = ""

    def __init__(self, session=None, rate_limit_delay=POLITE_DELAY_SECONDS):
        self.session = session or requests.Session()
        self.session.headers.update(self.default_headers())
        self.rate_limit_delay = rate_limit_delay
        self._last_request_at = 0.0

    @staticmethod
    def default_headers():
        """Realistic browser-like headers shared by all scrapers."""
        return {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def polite_get(self, url):
        """GET ``url`` with rate limiting and tenacity-backed retries.

        Raises:
            FetchBlockedError: when the site responds 401/403/429.
            requests.RequestException: after retries are exhausted.
        """
        retrying_get = retry(
            wait=wait_exponential(multiplier=1, min=2, max=30),
            stop=stop_after_attempt(4),
            retry=retry_if_exception(_retryable),
            reraise=True,
        )(self._get_once)
        return retrying_get(url)

    def _get_once(self, url):
        elapsed = time.monotonic() - self._last_request_at
        if self._last_request_at and elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self._last_request_at = time.monotonic()

        response = self.session.get(
            url, timeout=REQUEST_TIMEOUT_SECONDS, allow_redirects=True
        )
        if response.status_code in BLOCKED_STATUS_CODES:
            raise FetchBlockedError(
                f"{self.source_name} returned {response.status_code} "
                f"for {url}; backing off entirely."
            )
        response.raise_for_status()
        return response

    def check_robots_txt(self):
        """Fetch robots.txt and return whether our listing path is allowed.

        Documenting and honouring robots.txt is part of Phase 1; scrapers
        call this before fetching listing pages.
        """
        robots_url = self.base_url.rstrip("/") + "/robots.txt"
        try:
            response = self.session.get(robots_url, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            logger.warning("Could not fetch robots.txt for %s: %s", self.base_url, exc)
            return None
        if response.status_code != 200:
            return None
        return self._robots_allows(response.text)

    def _robots_allows(self, robots_text):
        """Simple robots.txt evaluation for a generic '*; Disallow' layout."""
        user_agent = None
        for raw_line in robots_text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "user-agent":
                user_agent = value
            elif key == "disallow" and user_agent == "*" and value:
                if value == "/":
                    logger.warning(
                        "%s robots.txt disallows all crawlers; scraping must not proceed.",
                        self.source_name,
                    )
                    return False
        return True

    @abstractmethod
    def build_listing_url(self, page: int) -> str:
        """Return the listing page URL for the given page number."""

    @abstractmethod
    def parse_listing(self, raw_item) -> Optional[CarListing]:
        """Parse one raw HTML item into a validated CarListing (or None)."""

    def fetch_listings(self, pages: int = 1) -> ScrapeResult:
        """Fetch and parse ``pages`` listing pages into a ScrapeResult."""
        all_listings = []
        warnings = []
        error = None
        requested_url = self.build_listing_url(1)

        allowed = self.check_robots_txt()
        if allowed is False:
            return ScrapeResult(
                source=self.source_name,
                requested_url=requested_url,
                error="robots.txt disallows crawling this site",
            )

        for page in range(1, pages + 1):
            url = self.build_listing_url(page)
            try:
                response = self.polite_get(url)
            except FetchBlockedError as exc:
                logger.error("%s blocked: %s", self.source_name, exc)
                error = str(exc)
                break
            except requests.RequestException as exc:
                logger.error("%s fetch failed for %s: %s", self.source_name, url, exc)
                warnings.append(f"page {page}: fetch failed ({exc.__class__.__name__})")
                continue

            listings, page_warnings = self._parse_page(response)
            logger.info(
                "%s page %d: %d listings parsed, %d warnings",
                self.source_name, page, len(listings), len(page_warnings),
            )
            all_listings.extend(listings)
            warnings.extend(page_warnings)

        return ScrapeResult(
            source=self.source_name,
            requested_url=requested_url,
            listings=all_listings,
            parse_warnings=warnings,
            error=error,
        )

    def _parse_page(self, response):
        """Parse a listing page into ``(listings, warnings)``."""
        soup = BeautifulSoup(response.content, "html.parser")
        items = soup.select(self.listing_selector())
        listings = []
        warnings = []
        for index, item in enumerate(items):
            try:
                listing = self.parse_listing(item)
            except Exception as exc:  # noqa: BLE001 - per-item isolation by design
                listing = None
                warnings.append(f"item {index}: parse error: {exc}")
            if listing is None:
                warnings.append(f"item {index}: missing required fields, dropped")
            else:
                listings.append(listing)
        return listings, warnings

    @abstractmethod
    def listing_selector(self) -> str:
        """CSS selector that matches individual listing cards on a page."""

    def to_schema(self, **fields) -> Optional[CarListing]:
        """Validate a candidate listing; return None (with warning) if invalid."""
        fields.setdefault("source", self.source_name)
        listing_id = fields.get("listing_id") or derive_listing_id(fields.get("url", ""))
        try:
            validated = CarListing.model_validate({**fields, "listing_id": listing_id})
        except Exception as exc:  # noqa: BLE001 - validation failures become warnings
            logger.warning("Listing failed schema validation: %s (%s)", fields.get("url"), exc)
            return None
        return validated
