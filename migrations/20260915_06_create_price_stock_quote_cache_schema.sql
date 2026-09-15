-- =========================================================
-- Homez OS
--
-- File : migrations/20260915_06_create_price_stock_quote_cache_schema.sql
--
-- 2026-09-15 전면 감사 후속(Phase 9A/9D/9E, HOMEZ_USER_OPERATION_
-- SETTINGS.md 7-8/8-5/8-6).
--
-- 신규 테이블 3개:
--   - price_cache_ttl_settings(8-5): 회사별 가격 캐시 유효시간(분),
--     append-only(최신 행이 현재 값). 기본값 30분(코드 상수).
--   - stock_cache_ttl_settings(8-6): 회사별 재고 캐시 유효시간(분),
--     동일한 패턴. 기본값 10분. 가격 TTL과 의도적으로 분리한 테이블.
--   - price_stock_quote_caches(7-8): 회사·연결계정·상품·옵션
--     단위로 격리된 "현재 상태" 캐시 1행(append-only 아님 — 같은
--     키로 다시 조회하면 갱신). 가격/재고가 각자 독립적인
--     confirmed_at/expires_at을 가진다.
--
-- 기존 테이블·데이터는 전혀 건드리지 않는다.
-- =========================================================

BEGIN;

CREATE TABLE price_cache_ttl_settings (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	ttl_minutes INTEGER NOT NULL,
	set_by INTEGER NOT NULL,
	set_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_price_cache_ttl_settings_id ON price_cache_ttl_settings (id);
CREATE INDEX ix_price_cache_ttl_settings_company_id ON price_cache_ttl_settings (company_id);
CREATE INDEX ix_price_cache_ttl_settings_set_at ON price_cache_ttl_settings (set_at);

CREATE TABLE stock_cache_ttl_settings (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	ttl_minutes INTEGER NOT NULL,
	set_by INTEGER NOT NULL,
	set_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_stock_cache_ttl_settings_id ON stock_cache_ttl_settings (id);
CREATE INDEX ix_stock_cache_ttl_settings_company_id ON stock_cache_ttl_settings (company_id);
CREATE INDEX ix_stock_cache_ttl_settings_set_at ON stock_cache_ttl_settings (set_at);

CREATE TABLE price_stock_quote_caches (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	connection_id INTEGER NOT NULL,
	product_code VARCHAR(100) NOT NULL,
	option_id VARCHAR(100) NOT NULL,
	price_amount FLOAT,
	price_confirmed_at DATETIME,
	price_expires_at DATETIME,
	in_stock BOOLEAN,
	stock_confirmed_at DATETIME,
	stock_expires_at DATETIME,
	source VARCHAR(20) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_price_stock_quote_cache_key UNIQUE (company_id, connection_id, product_code, option_id)
);

CREATE INDEX ix_price_stock_quote_caches_id ON price_stock_quote_caches (id);
CREATE INDEX ix_price_stock_quote_caches_company_id ON price_stock_quote_caches (company_id);
CREATE INDEX ix_price_stock_quote_caches_connection_id ON price_stock_quote_caches (connection_id);
CREATE INDEX ix_price_stock_quote_caches_price_expires_at ON price_stock_quote_caches (price_expires_at);
CREATE INDEX ix_price_stock_quote_caches_stock_expires_at ON price_stock_quote_caches (stock_expires_at);

COMMIT;

-- Rollback(수동, 실제 DB 적용 시 별도 승인 후에만 사용):
-- DROP TABLE IF EXISTS price_stock_quote_caches;
-- DROP TABLE IF EXISTS stock_cache_ttl_settings;
-- DROP TABLE IF EXISTS price_cache_ttl_settings;
