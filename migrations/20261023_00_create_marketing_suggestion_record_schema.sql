-- =========================================================
-- Homez OS
--
-- File : migrations/20261023_00_create_marketing_suggestion_record_schema.sql
--
-- 2026-10-23 마케팅 AI M4-full — 채택한 제안(상품명·검색어·가격)을 쿠팡 옵션 ID에 연결해 기록하는 신규 테이블 1개(추가형).
--   marketing_suggestion_records : 제안 1건(종류·값·반영일·채택 여부·옵션 ID)
-- Model(app/domains/ai_learning/model.py::MarketingSuggestionRecord)의 SQLite DDL 그대로다(시험이 대조한다).
-- 기존 테이블·데이터는 전혀 건드리지 않는다. 비밀값·고객 개인정보 없음.
-- 실제 DB 적용은 앱의 Migration 승인 화면에서 사용자가 승인할 때만(백업 후) 일어난다.
-- =========================================================

BEGIN;

CREATE TABLE marketing_suggestion_records (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	kind VARCHAR(20) NOT NULL,
	vendor_item_id VARCHAR(40),
	product_ref VARCHAR(80),
	suggested_value VARCHAR(300) NOT NULL,
	suggested_at DATETIME NOT NULL,
	adopted BOOLEAN NOT NULL,
	adopted_at DATETIME,
	created_by INTEGER,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_marketing_suggestion_records_company_id ON marketing_suggestion_records (company_id);

CREATE INDEX ix_marketing_suggestion_records_id ON marketing_suggestion_records (id);

CREATE INDEX ix_marketing_suggestion_records_vendor_item_id ON marketing_suggestion_records (vendor_item_id);

COMMIT;

-- rollback(자동 실행되지 않음):
-- DROP INDEX IF EXISTS ix_marketing_suggestion_records_vendor_item_id;
-- DROP INDEX IF EXISTS ix_marketing_suggestion_records_id;
-- DROP INDEX IF EXISTS ix_marketing_suggestion_records_company_id;
-- DROP TABLE IF EXISTS marketing_suggestion_records;
