"""Shared parsing helpers for turning scraped text into listing fields."""

import re

# Make -> plausible models, used to keep make/model extraction consistent.
MAKE_MODEL_MAP = {
    "Toyota": ["Axio", "Vitz", "Fielder", "Wish", "FJ Cruiser", "RAV-4"],
    "Honda": ["Fit", "Vezel", "Grace", "CRV"],
    "Mazda": ["Demio", "CX-5", "Axela", "Atenza", "3", "CX-3"],
    "Nissan": ["Note", "X-Trail", "Skyline"],
    "Subaru": ["Impreza", "Forester", "Legacy", "Outback"],
    "Mitsubishi": ["Mirage", "Outlander"],
}

_MODEL_TO_MAKE = {mod: make for make, models in MAKE_MODEL_MAP.items() for mod in models}


# Multi-word makes checked before single-token makes so "Land Rover Discovery"
# is not split into make="Land".
MULTI_WORD_MAKES = ("Land Rover", "Mercedes Benz", "Alfa Romeo", "Great Wall")

_YEAR_TOKEN = re.compile(r"^(19|20)\d{2}$")


def _word_match(candidate, title_upper):
    """Whole-word, case-insensitive containment test."""
    return re.search(rf"\b{re.escape(candidate.upper())}\b", title_upper) is not None


def _derive_model_from_tokens(tokens, start):
    """Join tokens between the make and the first year token (Jiji titles
    follow 'Make Model... YEAR COLOR'). Returns '' when nothing precedes
    the year."""
    model_tokens = []
    for token in tokens[start:]:
        if _YEAR_TOKEN.match(token):
            break
        model_tokens.append(token)
    return " ".join(model_tokens)


def _squash(text):
    """Lowercase with spaces and hyphens removed, for fuzzy make matching."""
    return text.upper().replace(" ", "").replace("-", "")


# Canonical display casing for makes, so 'TOYOTA' (SBT/CFJ titles) and
# 'toyota' (URL slugs) normalise to the same key as Jiji's 'Toyota'.
_MAKE_CANON = {
    _squash(name): name
    for name in [
        *MAKE_MODEL_MAP.keys(),
        "Land Rover",
        "Mercedes Benz",
        "Alfa Romeo",
        "Great Wall",
        "Suzuki",
        "Daihatsu",
        "Isuzu",
        "Lexus",
        "BMW",
        "Audi",
        "Volkswagen",
        "Volvo",
        "Jeep",
        "Mini",
        "Hyundai",
        "Kia",
        "Ford",
        "Chery",
        "Porsche",
        "Jaguar",
        "Peugeot",
        "Renault",
        "Fiat",
    ]
}


def normalize_make(raw):
    """Normalise a scraped make to canonical casing ('TOYOTA' -> 'Toyota')."""
    return _MAKE_CANON.get(_squash(raw or ""), (raw or "Unknown").strip().title())


def _match_known(title_upper):
    """Match a known make and model from the maps.

    Returns ``(make, model)`` where either may be None when not found. If
    the matched make has no known model, any other known model in the title
    claims the pair (so "Toyota Vezel" never pairs Toyota with a Honda
    model silently).
    """
    squashed_title = _squash(title_upper)
    make = None
    for candidate in MULTI_WORD_MAKES:
        if _squash(candidate) in squashed_title:
            make = candidate
            break
    if make is None:
        for candidate in MAKE_MODEL_MAP:
            if _word_match(candidate, title_upper):
                make = candidate
                break
    if make is not None:
        for candidate in MAKE_MODEL_MAP.get(make, []):
            if _word_match(candidate, title_upper):
                return make, candidate
    for candidate, owner in _MODEL_TO_MAKE.items():
        if _word_match(candidate, title_upper):
            return owner, candidate
    return make, None


def _make_token_start(tokens, make):
    """Index just past the tokens the make name consumes.

    Handles both one-token makes ("Mercedes-Benz") and two-token makes
    ("Land Rover"), falling back to first-word containment when the make
    does not sit at the title start.
    """
    make_norm = make.upper().replace("-", " ")
    accumulated = ""
    for index, token in enumerate(tokens):
        accumulated = f"{accumulated} {token.upper().replace('-', ' ')}".strip()
        if accumulated == make_norm:
            return index + 1
    for index, token in enumerate(tokens):
        if make_norm.split()[0] in token.upper():
            return index + 1
    return 1


def _derive_from_title(tokens, make):
    """Derive ``(make, model)`` from 'Make Model... YEAR COLOR' titles.

    Titles without any year token carry no anchor for the split, so they
    stay ("Unknown", "Unknown") rather than guessing.
    """
    if not any(_YEAR_TOKEN.match(token) for token in tokens):
        return "Unknown", "Unknown"
    if make is None:
        return tokens[0], _derive_model_from_tokens(tokens, 1) or "Unknown"
    start = _make_token_start(tokens, make)
    return make, _derive_model_from_tokens(tokens, start) or "Unknown"


def extract_make_model(title):
    """Extract ``(make, model)`` from a listing title, keeping them consistent.

    Strategy: match the known make/model maps first (word-boundary, with
    multi-word and hyphenated makes handled); if no known model matches,
    derive the model from the title tokens between the make and the first
    year token, e.g. "Mercedes-Benz E220 2016 Silver" ->
    ("Mercedes Benz", "E220"). Titles for makes outside the map still yield
    a sensible pair instead of ("Unknown", "Unknown").
    """
    tokens = (title or "").split()
    if not tokens:
        return "Unknown", "Unknown"
    make, model = _match_known((title or "").upper())
    if model is not None:
        return make, model
    return _derive_from_title(tokens, make)


def parse_int_from_text(text):
    """Return the first integer contained in ``text`` or ``None``."""
    if not text:
        return None
    match = re.search(r"\d[\d,]*", str(text))
    if not match:
        return None
    try:
        return int(match.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_year(text):
    """Extract a plausible model year (1990-2027) from ``text`` or ``None``."""
    if not text:
        return None
    for match in re.finditer(r"(19[9]\d|20[0-2]\d)", str(text)):
        year = int(match.group(0))
        if 1990 <= year <= 2027:
            return year
    return None


def parse_mileage_km(text):
    """Extract a plausible mileage in km (0-500,000) from ``text`` or ``None``."""
    value = parse_int_from_text(text)
    if value is None or value > 500_000:
        return None
    return value


def parse_price_jpy(text):
    """Extract a plausible JPY price (positive, < 20M) from ``text`` or ``None``."""
    value = parse_int_from_text(text)
    if value is None or value <= 0 or value > 20_000_000:
        return None
    return value


def parse_price_kes(text):
    """Extract a plausible KES price (positive, < 50M) from ``text`` or ``None``."""
    value = parse_int_from_text(text)
    if value is None or value <= 0 or value > 50_000_000:
        return None
    return value


def derive_listing_id(url):
    """Derive a stable listing id from a listing URL when possible.

    Jiji detail URLs look like
    ``.../mercedes-benz-e220-2016-silver-1zj6PH2tmIn1xvp386BUbiGC.html`` —
    the trailing dash token before ``.html`` is the stable advert id. Query
    parameters are deliberately dropped (they contain volatile position
    info), so the id is stable across pages and sessions.
    """
    text = str(url or "")
    match = re.search(r"-([A-Za-z0-9]{6,})\.html", text)
    if match:
        return match.group(1)
    match = re.search(r"-(\d{4,})(?:\.html?|/)?$", text)
    if match:
        return match.group(1)
    return text.split("?", 1)[0]


def coerce_engine_cc(text):
    """Extract an engine displacement in cc from ``text`` or ``None``."""
    if not text:
        return None
    match = re.search(r"(\d{3,4})\s*(?:cc)?", str(text), flags=re.IGNORECASE)
    if not match:
        return None
    value = int(match.group(1))
    return value if 100 <= value <= 10_000 else None


def coerce_fuel_type(text):
    """Normalise a fuel-type string or return ``None``."""
    if not text:
        return None
    lowered = str(text).lower()
    for token, label in [
        ("hybrid", "Hybrid"),
        ("diesel", "Diesel"),
        ("petrol", "Petrol"),
        ("gasoline", "Petrol"),
    ]:
        if token in lowered:
            return label
    return None


def coerce_transmission(text):
    """Normalise a transmission string or return ``None``."""
    if not text:
        return None
    lowered = str(text).lower()
    if "cvt" in lowered or "auto" in lowered:
        return "Automatic"
    if "manual" in lowered:
        return "Manual"
    return None


def coerce_body_type(text):
    """Normalise a body-type string or return ``None``."""
    if not text:
        return None
    lowered = str(text).lower()
    for token, label in [
        ("hatch", "Hatchback"),
        ("sedan", "Sedan"),
        ("suv", "SUV"),
        ("wagon", "Wagon"),
        ("pickup", "Pickup"),
    ]:
        if token in lowered:
            return label
    return None
