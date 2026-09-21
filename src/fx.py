"""Live JPY -> KES exchange-rate lookup with a conservative fallback.

The fallback is a documented constant, not silently invented data: callers
report it in API responses and logs so nobody mistakes it for a live rate.
Rates are cached briefly so batch jobs and interactive endpoints do not
re-query (or re-retry) the FX API for every single conversion.
"""

import logging
import time
from typing import Dict, Tuple

import requests

logger = logging.getLogger(__name__)

FX_API_URL = "https://api.exchangerate.host/latest"
# Documented static fallbacks used only when the FX API is unreachable.
DEFAULT_JPY_KES_RATE = 0.95
DEFAULT_USD_KES_RATE = 129.0
FALLBACK_IS_ESTIMATE = True

# (base, target) -> (rate, is_fallback, captured_at)
_FX_CACHE: Dict[Tuple[str, str], Tuple[float, bool, float]] = {}
_CACHE_TTL_LIVE_SECONDS = 600.0
_CACHE_TTL_FALLBACK_SECONDS = 60.0


def fetch_exchange_rate(base="JPY", target="KES", session=None, timeout=8, max_retries=3):
    """Fetch a live exchange rate, falling back to a documented constant.

    Results are cached: live rates for 10 minutes, fallbacks for 60 seconds
    (long enough to stop retry-storms, short enough to recover quickly).

    Returns:
        tuple: ``(rate, is_fallback)`` so callers can flag estimated rates.
    """
    session = session or requests.Session()
    now = time.time()
    cached = _FX_CACHE.get((base, target))
    if cached is not None:
        rate, is_fallback, captured_at = cached
        ttl = (
            _CACHE_TTL_FALLBACK_SECONDS if is_fallback else _CACHE_TTL_LIVE_SECONDS
        )
        if now - captured_at < ttl:
            return rate, is_fallback

    for attempt in range(1, max_retries + 1):
        try:
            resp = session.get(
                FX_API_URL,
                params={"base": base, "symbols": target},
                timeout=timeout,
            )
            if resp.status_code == 200:
                rate = resp.json().get("rates", {}).get(target)
                if rate:
                    _FX_CACHE[(base, target)] = (float(rate), False, now)
                    return float(rate), False
        except (requests.RequestException, ValueError) as exc:
            logger.debug("FX attempt %d failed: %s", attempt, exc)
    fallback = DEFAULT_JPY_KES_RATE if base == "JPY" else DEFAULT_USD_KES_RATE
    _FX_CACHE[(base, target)] = (fallback, True, now)
    logger.warning(
        "FX API unreachable after %d attempts; using documented fallback "
        "rate %s %s/KES (flagged as estimate).",
        max_retries,
        fallback,
        base,
    )
    return fallback, FALLBACK_IS_ESTIMATE


def jpy_to_kes(amount_jpy, rate):
    """Convert a JPY amount to KES, rounding to whole shillings."""
    return int(round(amount_jpy * rate))


def usd_to_kes(amount_usd, rate):
    """Convert a USD amount to KES, rounding to whole shillings."""
    return int(round(amount_usd * rate))
