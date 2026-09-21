-- 002_add_currency_columns.sql: support exporter prices quoted in USD/JPY.
-- Safe on SQLite (ALTER TABLE ADD COLUMN) and Postgres alike.

ALTER TABLE listings ADD COLUMN price_usd INTEGER;
ALTER TABLE listings ADD COLUMN currency VARCHAR(10);
ALTER TABLE listings_history ADD COLUMN price_usd INTEGER;
ALTER TABLE listings_history ADD COLUMN currency VARCHAR(10);
