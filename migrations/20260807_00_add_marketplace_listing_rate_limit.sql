-- Purpose: Gate H(2026-08-07) — Retry-After 정규화 영속화.
-- Source of Truth: app/domains/marketplace_listing/model.py
--
-- 이전 Migration(20260805_00_add_marketplace_listing_status_sync.sql)은
-- 이미 실제 homez.db에 APPLIED로 기록돼 있다(schema_migrations,
-- checksum e8399dc8...c43e184 — 이 사실은 이번 Gate H 작업 중 읽기
-- 전용으로 재확인했다). 그 파일은 checksum이 고정돼 있어 내용을
-- 수정하면 다음 diagnose()에서 ChecksumMismatchError가 발생한다 —
-- 그래서 그 파일을 건드리지 않고 이 신규 파일로 순수 추가만 한다
-- (기존 컬럼·테이블·인덱스는 전혀 건드리지 않는다).
--
-- 대상: marketplace_listings에 컬럼 2개 추가.
--   - rate_limit_retry_after_seconds: 가장 최근에 관측한 원본 초 값
--     (감사·CSV 표시용).
--   - rate_limit_retry_available_at: 실제로 재시도를 막는 데 쓰이는
--     유효 시각(naive UTC) — out-of-order 방어로 항상 더 늦은 값만
--     유지된다.
-- 둘 다 Model에서 nullable=True이고 NOT NULL 제약이 없으므로 backfill
-- DEFAULT가 필요 없다(기존 행은 그대로 NULL — "아직 rate limit
-- 상태가 없다"는 뜻과 정확히 일치한다).
--
-- 이 Migration은 실제 homez.db에 적용하지 않는다. 임시 DB 리허설로만
-- 검증했다(LIVE_GATE_QUEUE 대기).

BEGIN;

ALTER TABLE marketplace_listings ADD COLUMN rate_limit_retry_after_seconds INTEGER;
ALTER TABLE marketplace_listings ADD COLUMN rate_limit_retry_available_at DATETIME;

CREATE INDEX ix_marketplace_listings_rate_limit_retry_available_at ON marketplace_listings (rate_limit_retry_available_at);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로 실행,
-- 단 SQLite는 구버전에서 DROP COLUMN을 지원하지 않을 수 있으므로
-- 컬럼 제거는 테이블 재생성이 필요할 수 있다 — 실제 rollback 전 별도
-- 검토 필요):
-- BEGIN;
-- DROP INDEX ix_marketplace_listings_rate_limit_retry_available_at;
-- -- ALTER TABLE ... DROP COLUMN은 SQLite 3.35.0+ 에서만 지원.
-- ALTER TABLE marketplace_listings DROP COLUMN rate_limit_retry_available_at;
-- ALTER TABLE marketplace_listings DROP COLUMN rate_limit_retry_after_seconds;
-- COMMIT;
