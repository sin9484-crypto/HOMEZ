-- =========================================================
-- Homez OS
--
-- File : migrations/20260916_01_create_order_auto_collection_state_schema.sql
--
-- 2026-09-16 개인 베타 잔여 작업(Phase 6, HOMEZ_USER_OPERATION_SETTINGS.md
-- 2-8) — 회사 단위 자동 주문 감지 스케줄러 실행 이력. "사용 여부"는
-- 기존 automation_safety의 FunctionCode.ORDER_COLLECTION 함수모드를
-- 그대로 쓴다(중복 플래그 없음) — 이 테이블은 주기·마지막 시도/성공
-- 시각·연속 실패 횟수만 기록한다.
--
-- 기존 테이블·데이터는 전혀 건드리지 않는다.
-- =========================================================

BEGIN;

CREATE TABLE order_auto_collection_states (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	interval_minutes INTEGER NOT NULL,
	last_attempted_at DATETIME,
	last_succeeded_at DATETIME,
	last_status VARCHAR(40),
	last_skip_reason VARCHAR(50),
	last_error_summary VARCHAR(300),
	consecutive_failure_count INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_order_auto_collection_state_company UNIQUE (company_id)
);

CREATE INDEX ix_order_auto_collection_states_company_id ON order_auto_collection_states (company_id);

COMMIT;

-- Rollback(수동, 실제 DB 적용 시 별도 승인 후에만 사용):
-- DROP TABLE IF EXISTS order_auto_collection_states;
