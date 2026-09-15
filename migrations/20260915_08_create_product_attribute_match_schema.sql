-- =========================================================
-- Homez OS
--
-- File : migrations/20260915_08_create_product_attribute_match_schema.sql
--
-- 2026-09-15 전면 감사 후속(Phase 9G, HOMEZ_USER_OPERATION_SETTINGS.md
-- 10-4 — "상품 속성(이름/옵션/수량/사이즈/제조사/원산지)을 매입처·
-- 후보(candidate)·판매채널 세 데이터 소스 사이에서 정규화해 비교하고,
-- 필수 항목이 불일치하거나 확인 불가면 자동 등록/자동 발주를 막는다").
--
-- product_attribute_comparison_runs: 비교 1회 실행의 헤더 행(현재
-- 상태가 아니라 이력 — 같은 상품을 다시 비교하면 새 행이 추가된다).
-- product_attribute_comparison_items: 그 실행에 포함된 필드별 상세
-- (매입처/판매채널/HOMEZ 현재 값과 각각의 출처·확인시각, 판정
-- 결과, 사람이 고른 선택값).
--
-- 기존 테이블·데이터는 전혀 건드리지 않는다.
-- =========================================================

BEGIN;

CREATE TABLE product_attribute_comparison_runs (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	product_identifier VARCHAR(200) NOT NULL,
	connection_id INTEGER,
	overall_status VARCHAR(20) NOT NULL,
	triggered_by INTEGER,
	created_at DATETIME NOT NULL,
	resolved_by INTEGER,
	resolved_at DATETIME,
	resolution_note VARCHAR(500),
	PRIMARY KEY (id)
);

CREATE INDEX ix_product_attribute_comparison_runs_id ON product_attribute_comparison_runs (id);
CREATE INDEX ix_product_attribute_comparison_runs_company_id ON product_attribute_comparison_runs (company_id);
CREATE INDEX ix_product_attribute_comparison_runs_product_identifier ON product_attribute_comparison_runs (product_identifier);
CREATE INDEX ix_product_attribute_comparison_runs_connection_id ON product_attribute_comparison_runs (connection_id);
CREATE INDEX ix_product_attribute_comparison_runs_overall_status ON product_attribute_comparison_runs (overall_status);

CREATE TABLE product_attribute_comparison_items (
	id INTEGER NOT NULL,
	run_id INTEGER NOT NULL,
	field_name VARCHAR(30) NOT NULL,
	supplier_value VARCHAR(300),
	supplier_source VARCHAR(100),
	supplier_confirmed_at DATETIME,
	sales_channel_value VARCHAR(300),
	sales_channel_source VARCHAR(100),
	sales_channel_confirmed_at DATETIME,
	homez_current_value VARCHAR(300),
	homez_current_source VARCHAR(100),
	homez_current_confirmed_at DATETIME,
	match_status VARCHAR(20) NOT NULL,
	selected_value VARCHAR(300),
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES product_attribute_comparison_runs (id)
);

CREATE INDEX ix_product_attribute_comparison_items_id ON product_attribute_comparison_items (id);
CREATE INDEX ix_product_attribute_comparison_items_run_id ON product_attribute_comparison_items (run_id);

COMMIT;

-- Rollback(수동, 실제 DB 적용 시 별도 승인 후에만 사용):
-- DROP TABLE IF EXISTS product_attribute_comparison_items;
-- DROP TABLE IF EXISTS product_attribute_comparison_runs;
