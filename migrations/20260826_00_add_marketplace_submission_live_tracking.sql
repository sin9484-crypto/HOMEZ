-- HOMEZ V7 Coupang live submission tracking (additive only).
ALTER TABLE marketplace_submissions
    ADD COLUMN request_fingerprint VARCHAR(64);
ALTER TABLE marketplace_submissions
    ADD COLUMN correlation_id VARCHAR(64);
ALTER TABLE marketplace_submissions
    ADD COLUMN external_http_status INTEGER;

CREATE INDEX IF NOT EXISTS ix_marketplace_submissions_request_fingerprint
    ON marketplace_submissions (request_fingerprint);
CREATE INDEX IF NOT EXISTS ix_marketplace_submissions_correlation_id
    ON marketplace_submissions (correlation_id);
