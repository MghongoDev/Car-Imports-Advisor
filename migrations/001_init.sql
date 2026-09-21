-- 001_init.sql: initial schema (reference DDL).
-- Tables are normally created via SQLAlchemy metadata (src.db_utils.init_db);
-- this file documents the schema and covers raw-SQL deployment paths.
-- Dialect note: this DDL is deliberately common-syntax (no SERIAL/NOW()) so
-- it runs on both SQLite (dev) and Postgres/Neon (deployed).

CREATE TABLE IF NOT EXISTS listings (
    listing_id VARCHAR(255) PRIMARY KEY,
    source VARCHAR(100),
    url VARCHAR(500),
    make VARCHAR(100),
    model VARCHAR(100),
    year INTEGER,
    mileage_km INTEGER,
    engine_cc INTEGER,
    fuel_type VARCHAR(50),
    transmission VARCHAR(50),
    body_type VARCHAR(50),
    price_jpy INTEGER,
    price_kes INTEGER,
    exchange_rate FLOAT,
    price_usd INTEGER,
    currency VARCHAR(10),
    content_hash VARCHAR(64),
    parse_warnings TEXT,
    scraped_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS listings_history (
    id INTEGER PRIMARY KEY,
    listing_id VARCHAR(255) NOT NULL,
    source VARCHAR(100),
    url VARCHAR(500),
    make VARCHAR(100),
    model VARCHAR(100),
    year INTEGER,
    mileage_km INTEGER,
    engine_cc INTEGER,
    price_jpy INTEGER,
    price_kes INTEGER,
    price_usd INTEGER,
    currency VARCHAR(10),
    content_hash VARCHAR(64),
    title VARCHAR(500),
    description TEXT,
    parse_warnings TEXT,
    scraped_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_listings_history_listing_id ON listings_history (listing_id);
CREATE INDEX IF NOT EXISTS ix_listings_history_content_hash ON listings_history (content_hash);

CREATE TABLE IF NOT EXISTS price_events (
    id INTEGER PRIMARY KEY,
    listing_id VARCHAR(255) NOT NULL,
    source VARCHAR(100),
    old_price_kes INTEGER,
    new_price_kes INTEGER,
    delta_kes INTEGER,
    delta_pct FLOAT,
    detected_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_price_events_listing_id ON price_events (listing_id);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    status VARCHAR(20),
    listings_scraped INTEGER,
    listings_new INTEGER,
    price_changes INTEGER,
    parse_warnings INTEGER,
    error TEXT
);
