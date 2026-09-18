-- =========================================================
-- Homez OS
--
-- File : migrations/20260918_00_create_order_collection_test_budget_usage_schema.sql
--
-- 2026-09-18 Phase 7B 실제 테스트 주문 검증 — 시험 전용 호출 예산의
-- 누적 사용량을 프로세스 재시작·반복 클릭·동시 요청에도 안전하게
-- 강제하기 위한 최소 테이블. max_pages=1(한 번의 실행이 몇 페이지를
-- 받는지)과는 독립적으로, "총 몇 번 실제로 조회를 시도했는지"를
-- 센다. 실제 provider.collect() 호출 직전에만 1 증가한다 —
-- EmergencyStop·Migration 제한 모드·자격증명 오류 등으로 막힌 시도는
-- 세지 않는다.
--
-- 기존 테이블·데이터는 전혀 건드리지 않는다.
-- =========================================================

BEGIN;

CREATE TABLE order_collection_test_budget_usages (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	store_connection_id INTEGER NOT NULL,
	channel_status VARCHAR(30) NOT NULL,
	get_calls_used INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_order_collection_test_budget_usage_scope UNIQUE (company_id, store_connection_id, channel_status)
);

CREATE INDEX ix_order_collection_test_budget_usages_id ON order_collection_test_budget_usages (id);
CREATE INDEX ix_order_collection_test_budget_usages_company_id ON order_collection_test_budget_usages (company_id);
CREATE INDEX ix_order_collection_test_budget_usages_store_connection_id ON order_collection_test_budget_usages (store_connection_id);

COMMIT;

-- Rollback(수동, 실제 DB 적용 시 별도 승인 후에만 사용):
-- DROP TABLE IF EXISTS order_collection_test_budget_usages;
