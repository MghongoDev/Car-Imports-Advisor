"""Database layer: SQLAlchemy ORM models, migrations, and query helpers.

Tables:
- ``listings``: latest snapshot per listing (what the API/UI serves).
- ``listings_history``: append-only observation history (SCD2-style CDC).
- ``price_events``: detected price changes between scrapes.
- ``pipeline_runs``: log of scrape/pipeline executions, surfaced on /health.

The database URL comes from ``DATABASE_URL`` (Neon Postgres in deployment)
and falls back to a local SQLite file for development.
"""

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    desc,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from sqlalchemy.types import JSON

from src.fx import DEFAULT_JPY_KES_RATE
from src.schemas import compute_content_hash, listing_to_row

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
MIGRATIONS_DIR = os.path.join(BASE_DIR, "migrations")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "raw_cars.db")
DEFAULT_DATABASE_URL = f"sqlite:///{DB_PATH}"


def _load_dotenv(path: Optional[str] = None) -> None:
    """Load KEY=VALUE pairs from ``.env`` without clobbering real env vars.

    A minimal reader (no third-party dependency): strips optional quotes,
    ignores comments and blank lines. Variables already present in the real
    environment always win, so CI/deployment secrets override local ones.
    """
    env_path = path or os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError as exc:
        logger.warning("Could not read %s: %s", env_path, exc)


_load_dotenv()

Base = declarative_base()

_ENGINES: Dict[str, Engine] = {}


def _utcnow() -> datetime:
    """Naive UTC timestamp (SQLite-friendly, unambiguous in Postgres)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ListingLatest(Base):
    """Latest snapshot of a car listing (one row per listing_id)."""

    __tablename__ = "listings"

    listing_id = Column(String(255), primary_key=True)
    source = Column(String(100))
    url = Column(String(500))
    make = Column(String(100))
    model = Column(String(100))
    year = Column(Integer)
    mileage_km = Column(Integer)
    engine_cc = Column(Integer)
    fuel_type = Column(String(50))
    transmission = Column(String(50))
    body_type = Column(String(50))
    price_jpy = Column(Integer)
    price_kes = Column(Integer)
    exchange_rate = Column(Float)
    price_usd = Column(Integer)
    currency = Column(String(10))
    content_hash = Column(String(64))
    parse_warnings = Column(JSON)
    scraped_at = Column(DateTime)


class ListingsHistory(Base):
    """Append-only observation history per listing (SCD2-style CDC)."""

    __tablename__ = "listings_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    listing_id = Column(String(255), index=True, nullable=False)
    source = Column(String(100))
    url = Column(String(500))
    make = Column(String(100))
    model = Column(String(100))
    year = Column(Integer)
    mileage_km = Column(Integer)
    engine_cc = Column(Integer)
    price_jpy = Column(Integer)
    price_kes = Column(Integer)
    price_usd = Column(Integer)
    currency = Column(String(10))
    exchange_rate = Column(Float)
    content_hash = Column(String(64), index=True)
    title = Column(String(500))
    description = Column(Text)
    parse_warnings = Column(JSON)
    scraped_at = Column(DateTime, default=_utcnow)


class PriceEvent(Base):
    """A detected price change between two observations of a listing."""

    __tablename__ = "price_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    listing_id = Column(String(255), index=True, nullable=False)
    source = Column(String(100))
    old_price_kes = Column(Integer)
    new_price_kes = Column(Integer)
    delta_kes = Column(Integer)
    delta_pct = Column(Float)
    detected_at = Column(DateTime, default=_utcnow)


class PipelineRun(Base):
    """Log of pipeline executions, surfaced on the /health endpoint."""

    __tablename__ = "pipeline_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(DateTime, default=_utcnow)
    finished_at = Column(DateTime)
    status = Column(String(20))
    listings_scraped = Column(Integer)
    listings_new = Column(Integer)
    price_changes = Column(Integer)
    parse_warnings = Column(Integer)
    error = Column(Text)



def _normalise_postgres_url(url: str) -> str:
    """Point plain postgres URLs at the installed psycopg 3 driver."""
    if url.startswith(("postgresql://", "postgres://")):
        return url.replace("postgresql://", "postgresql+psycopg://", 1).replace(
            "postgres://", "postgresql+psycopg://", 1
        )
    return url


def get_engine(database_url: Optional[str] = None):
    """Create (and memoise) the SQLAlchemy engine for the given URL."""
    url = _normalise_postgres_url(
        database_url or os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    )
    engine = _ENGINES.get(url)
    if engine is None:
        kwargs: Dict[str, Any] = {"future": True}
        if url.startswith("postgresql+"):
            # Managed Postgres (Neon) is reached through pooled endpoints:
            # pre-ping drops connections killed server-side while idle, a
            # small bounded pool suits serverless/web workloads, and idle
            # connections are recycled before platform limits kick in.
            kwargs.update(
                pool_pre_ping=True,
                pool_size=5,
                max_overflow=10,
                pool_recycle=1800,
            )
        engine = create_engine(url, **kwargs)
        _ENGINES[url] = engine
    return engine


def make_session_factory(database_url: Optional[str] = None):
    """Return a sessionmaker bound to the project engine."""
    return sessionmaker(bind=get_engine(database_url), future=True, expire_on_commit=False)


def init_db(database_url: Optional[str] = None):
    """Create all tables if they do not exist; return the engine."""
    engine = get_engine(database_url)
    Base.metadata.create_all(engine)
    return engine


_ADD_COLUMN_RE = re.compile(
    r"ALTER\s+TABLE\s+[\"'`]?([\w.]+)[\"'`]?\s+ADD\s+COLUMN\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?[\"'`]?(\w+)[\"'`]?",
    re.IGNORECASE,
)


def _existing_columns(engine: Engine) -> Dict[str, set]:
    """Map of table name -> set of column names, from the live schema."""
    inspector = inspect(engine)
    return {
        table: {col["name"] for col in inspector.get_columns(table)}
        for table in inspector.get_table_names()
    }


def run_migrations(engine: Engine) -> None:
    """Apply numbered SQL migrations from ``migrations/`` (idempotent).

    Each migration runs inside a transaction and is recorded in the
    ``schema_migrations`` table so it never applies twice. ``ADD COLUMN``
    statements for columns the ORM already created are skipped up front
    (Postgres aborts a transaction after any error, so failures cannot be
    tolerated reactively). This is a lightweight alternative to Alembic,
    appropriate at this project's scale.
    """
    with engine.connect() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "name VARCHAR(255) PRIMARY KEY, applied_at TIMESTAMP)"
        )
        conn.commit()
        applied = {
            row[0]
            for row in conn.exec_driver_sql("SELECT name FROM schema_migrations").fetchall()
        }
        if not os.path.isdir(MIGRATIONS_DIR):
            return
        columns = _existing_columns(engine)
        for name in sorted(os.listdir(MIGRATIONS_DIR)):
            if not name.endswith(".sql") or name in applied:
                continue
            path = os.path.join(MIGRATIONS_DIR, name)
            with open(path, "r", encoding="utf-8") as handle:
                sql_text = handle.read()
            logger.info("Applying migration %s", name)
            for statement in _split_sql_statements(sql_text):
                match = _ADD_COLUMN_RE.match(statement)
                if match and match.group(1) in columns:
                    table, column = match.group(1), match.group(2)
                    if column.lower() in columns[table]:
                        logger.debug(
                            "Migration %s: %s.%s exists, skipping",
                            name, table, column,
                        )
                        continue
                conn.exec_driver_sql(statement)
            conn.execute(
                text("INSERT INTO schema_migrations (name, applied_at) "
                     "VALUES (:name, CURRENT_TIMESTAMP)"),
                {"name": name},
            )
            conn.commit()


def _split_sql_statements(sql_text: str) -> List[str]:
    """Split a SQL script into individual statements (naive but sufficient
    for our migration files, which contain no procedural blocks). Comment
    lines are stripped rather than allowed to swallow the next statement."""
    cleaned_lines = [
        line for line in sql_text.splitlines()
        if not line.strip().startswith("--")
    ]
    statements = [chunk.strip() for chunk in "\n".join(cleaned_lines).split(";")]
    return [chunk for chunk in statements if chunk]


def _listing_to_row(listing) -> Dict:
    """Canonical column mapping (delegates to the shared schema helper)."""
    row = listing_to_row(listing)
    if not row.get("scraped_at"):
        row["scraped_at"] = _utcnow()
    return row


def upsert_latest_listing(session: Session, listing) -> None:
    """Insert or update the latest-snapshot row for a listing."""
    values = _listing_to_row(listing)
    row = session.get(ListingLatest, values["listing_id"])
    if row is None:
        row = ListingLatest(**values)
        session.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)


def append_history(session: Session, listing) -> None:
    """Append an immutable observation row to the listing history table.

    The CDC content hash is computed here if not already supplied, so every
    history row carries the hash that change detection compares against.
    """
    values = _listing_to_row(listing)
    if not values.get("content_hash"):
        values["content_hash"] = compute_content_hash(values)

    allowed = set(ListingsHistory.__table__.columns.keys())
    filtered = {key: value for key, value in values.items() if key in allowed}
    session.add(ListingsHistory(**filtered))


def record_price_event(session: Session, event) -> None:
    """Persist a detected price change event."""
    session.add(
        PriceEvent(
            listing_id=event.listing_id,
            source=getattr(event, "source", None),
            old_price_kes=event.old_price_kes,
            new_price_kes=event.new_price_kes,
            delta_kes=event.delta_kes,
            delta_pct=event.delta_pct,
        )
    )


def store_dataframe(df: pd.DataFrame, table_name="car_listings") -> None:
    """Legacy helper: store a DataFrame in a plain table (kept for demos)."""
    engine = get_engine()
    df.to_sql(table_name, engine, if_exists="replace", index=False)
    logger.info("Stored %d rows in %s", len(df), table_name)


def load_dataframe(table_name="car_listings") -> pd.DataFrame:
    """Legacy helper: read a plain table into a DataFrame (kept for demos)."""
    engine = get_engine()
    try:
        return pd.read_sql(f"SELECT * FROM {table_name}", engine)
    except Exception:  # noqa: BLE001 - missing table returns empty frame
        return pd.DataFrame()


def _row_to_dict(row) -> Dict:
    """Convert an ORM row to a plain dict, dropping SQLAlchemy state."""
    return {key: value for key, value in row.__dict__.items() if not key.startswith("_")}


def fetch_latest_listings(limit: int = 100) -> List[Dict]:
    """Return the most recent listings from the latest-snapshot table."""
    with make_session_factory()() as session:
        stmt = select(ListingLatest).order_by(desc(ListingLatest.scraped_at)).limit(limit)
        rows = session.execute(stmt).scalars().all()
        return [_row_to_dict(row) for row in rows]


def fetch_listing_history(listing_id: str) -> List[Dict]:
    """Return the full observation history for one listing."""
    with make_session_factory()() as session:
        stmt = (
            select(ListingsHistory)
            .where(ListingsHistory.listing_id == listing_id)
            .order_by(ListingsHistory.scraped_at)
        )
        rows = session.execute(stmt).scalars().all()
        return [_row_to_dict(row) for row in rows]


def fetch_price_events(limit: int = 50) -> List[Dict]:
    """Return the most recent price-change events."""
    with make_session_factory()() as session:
        stmt = select(PriceEvent).order_by(desc(PriceEvent.detected_at)).limit(limit)
        rows = session.execute(stmt).scalars().all()
        return [_row_to_dict(row) for row in rows]


def last_pipeline_run() -> Optional[Dict]:
    """Return the most recent pipeline run row, or None if never run."""
    with make_session_factory()() as session:
        stmt = select(PipelineRun).order_by(desc(PipelineRun.started_at)).limit(1)
        row = session.execute(stmt).scalars().first()
        return _row_to_dict(row) if row else None


def fetch_price_stream_frame(limit: int = 2000) -> pd.DataFrame:
    """Latest observation per listing from CDC history as a DataFrame.

    This is the live price stream used for drift monitoring. Prices are
    normalised to JPY (training reference units): KES observations convert
    at their recorded exchange rate, USD via an approximate rate flagged in
    the logs — enough fidelity for distribution comparison, never for
    pricing.
    """
    with make_session_factory()() as session:
        rows = session.execute(
            select(ListingsHistory).order_by(
                ListingsHistory.listing_id, desc(ListingsHistory.scraped_at)
            )
        ).scalars().all()
    latest: Dict[str, Dict] = {}
    for row in rows:
        item = _row_to_dict(row)
        latest.setdefault(item["listing_id"], item)
    frame = pd.DataFrame(list(latest.values()))
    if frame.empty:
        return frame

    def to_jpy(item: pd.Series) -> Optional[float]:
        # NB: pandas missing values are NaN, which is truthy in Python —
        # always test with pd.notna, never a bare ``if value:``.
        price_jpy = item.get("price_jpy")
        if price_jpy is not None and pd.notna(price_jpy):
            return float(price_jpy)
        price_kes = item.get("price_kes")
        if price_kes is not None and pd.notna(price_kes):
            rate = item.get("exchange_rate")
            if rate is not None and pd.notna(rate):
                return float(price_kes) / float(rate)
            # Legacy history rows predate the exchange_rate column: convert
            # at the documented default rate, flagged in the logs.
            logger.debug(
                "History row %s lacks exchange_rate; using default %s",
                item.get("listing_id"), DEFAULT_JPY_KES_RATE,
            )
            return float(price_kes) / DEFAULT_JPY_KES_RATE
        price_usd = item.get("price_usd")
        if price_usd is not None and pd.notna(price_usd):
            # Rough USD->JPY for distribution monitoring only (flagged).
            return float(price_usd) * 150.0
        return None

    frame["price_jpy"] = frame.apply(to_jpy, axis=1)
    frame = frame.dropna(subset=["price_jpy"])
    if len(frame) > limit:
        frame = frame.head(limit)
    return frame.reset_index(drop=True)
