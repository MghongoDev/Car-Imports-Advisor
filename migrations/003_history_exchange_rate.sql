-- Add the observation-time JPY/KES rate to the CDC history table so KES
-- observations can be normalised to JPY for drift monitoring.
ALTER TABLE listings_history ADD COLUMN exchange_rate REAL;
