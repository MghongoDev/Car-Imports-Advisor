"""Scraper package: base interface plus concrete marketplace scrapers.

Every scraper returns a :class:`~src.schemas.ScrapeResult` and never falls
back to mock data on failure. Failures are surfaced loudly via logging and
the ``error`` field so callers decide what to do.
"""

from src.scrapers.aaajapan import AaaJapanScraper
from src.scrapers.base import BaseScraper, FetchBlockedError
from src.scrapers.beforward import BeforwardScraper
from src.scrapers.carfromjapan import CarFromJapanScraper
from src.scrapers.japanesecartrade import JapaneseCarTradeScraper
from src.scrapers.jiji import JijiScraper
from src.scrapers.sbtjapan import SbtJapanScraper

__all__ = [
    "BaseScraper",
    "FetchBlockedError",
    "JijiScraper",
    "SbtJapanScraper",
    "BeforwardScraper",
    "CarFromJapanScraper",
    "AaaJapanScraper",
    "JapaneseCarTradeScraper",
]
