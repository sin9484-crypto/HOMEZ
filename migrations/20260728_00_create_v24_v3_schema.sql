-- Purpose: HOMEZ V2.4 Commerce Safety Layer + V3 Discovery Core — 전체 스키마 생성.
-- Source of Truth: app/domains/automation_safety/model.py,
--                   app/domains/product_candidate/model.py
--
-- 대상 8개 테이블 (Model 기준, ForeignKey 없음 — 전부 논리 참조 컬럼):
--   automation_mode_states
--   emergency_stops
--   execution_limits
--   execution_usages
--   execution_period_usages
--   product_candidates
--   product_candidate_evidences
--   product_candidate_decisions
--
-- 주의: 원 요청 목록은 7개 테이블(execution_period_usages 제외)이었으나,
-- V2.4 감사 대응(실행 한도 원자적 처리)을 위해 app/domains/automation_safety/
-- model.py에 ExecutionPeriodUsage를 추가했다. 이 Migration은 실제 Model을
-- Source of Truth로 삼으므로 8개 테이블 전부를 포함한다(누락 시
-- Model↔Migration 자동 드리프트 테스트가 실패한다).
--
-- 실행 전 필수 확인: 아래 8개 테이블이 대상 DB에 하나도 존재하지 않아야 한다.
-- 일부만 존재하는 상태(부분 적용)를 이 스크립트는 감지하지 않고 그대로
-- CREATE TABLE을 시도해 실패하도록 둔다(IF NOT EXISTS를 쓰지 않음).
--
-- 이 Migration은 실제 homez.db에 적용하지 않는다. 임시 DB에서만 검증한다.

BEGIN;

CREATE TABLE automation_mode_states (
	id INTEGER NOT NULL,
	mode VARCHAR(30) NOT NULL,
	set_by INTEGER NOT NULL,
	reason VARCHAR(500),
	set_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_automation_mode_states_id ON automation_mode_states (id);
CREATE INDEX ix_automation_mode_states_set_at ON automation_mode_states (set_at);

CREATE TABLE emergency_stops (
	id INTEGER NOT NULL,
	is_active BOOLEAN NOT NULL,
	reason VARCHAR(500) NOT NULL,
	set_by INTEGER NOT NULL,
	set_at DATETIME NOT NULL,
	cleared_by INTEGER,
	cleared_at DATETIME,
	audit_ref VARCHAR(200),
	PRIMARY KEY (id)
);

CREATE INDEX ix_emergency_stops_id ON emergency_stops (id);
CREATE INDEX ix_emergency_stops_is_active ON emergency_stops (is_active);
CREATE INDEX ix_emergency_stops_set_at ON emergency_stops (set_at);

CREATE TABLE execution_limits (
	id INTEGER NOT NULL,
	product_id INTEGER,
	daily_funding_limit FLOAT,
	per_product_funding_limit FLOAT,
	daily_quantity_limit INTEGER,
	per_product_quantity_limit INTEGER,
	currency VARCHAR(10) NOT NULL,
	period VARCHAR(20) NOT NULL,
	active BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_execution_limits_active ON execution_limits (active);
CREATE INDEX ix_execution_limits_id ON execution_limits (id);
CREATE INDEX ix_execution_limits_product_id ON execution_limits (product_id);

CREATE TABLE execution_usages (
	id INTEGER NOT NULL,
	product_id INTEGER,
	funding_amount FLOAT NOT NULL,
	quantity INTEGER NOT NULL,
	idempotency_key VARCHAR(150) NOT NULL,
	occurred_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_execution_usages_idempotency UNIQUE (idempotency_key)
);

CREATE INDEX ix_execution_usages_id ON execution_usages (id);
CREATE INDEX ix_execution_usages_occurred_at ON execution_usages (occurred_at);
CREATE INDEX ix_execution_usages_product_id ON execution_usages (product_id);

CREATE TABLE execution_period_usages (
	id INTEGER NOT NULL,
	scope_key VARCHAR(120) NOT NULL,
	period_start DATE NOT NULL,
	consumed_funding FLOAT NOT NULL,
	consumed_quantity INTEGER NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_execution_period_usage_scope_period UNIQUE (scope_key, period_start)
);

CREATE INDEX ix_execution_period_usages_id ON execution_period_usages (id);
CREATE INDEX ix_execution_period_usages_period_start ON execution_period_usages (period_start);
CREATE INDEX ix_execution_period_usages_scope_key ON execution_period_usages (scope_key);

CREATE TABLE product_candidates (
	id INTEGER NOT NULL,
	candidate_key VARCHAR(300) NOT NULL,
	source_type VARCHAR(50) NOT NULL,
	source_reference VARCHAR(200) NOT NULL,
	market VARCHAR(30) NOT NULL,
	product_name VARCHAR(300) NOT NULL,
	category_hint VARCHAR(100),
	brand_hint VARCHAR(100),
	discovered_at DATETIME NOT NULL,
	release_date DATE,
	is_new_product BOOLEAN,
	trend_score FLOAT,
	novelty_score FLOAT,
	demand_score FLOAT,
	competition_score FLOAT,
	margin_score FLOAT,
	risk_score FLOAT,
	confidence FLOAT,
	evidence_summary VARCHAR(2000),
	status VARCHAR(20) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_product_candidates_candidate_key UNIQUE (candidate_key)
);

CREATE INDEX ix_product_candidates_id ON product_candidates (id);
CREATE INDEX ix_product_candidates_market ON product_candidates (market);
CREATE INDEX ix_product_candidates_source_type ON product_candidates (source_type);
CREATE INDEX ix_product_candidates_status ON product_candidates (status);

CREATE TABLE product_candidate_evidences (
	id INTEGER NOT NULL,
	candidate_id INTEGER NOT NULL,
	evidence_type VARCHAR(50) NOT NULL,
	payload_summary VARCHAR(2000) NOT NULL,
	score FLOAT,
	confidence FLOAT,
	recorded_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_product_candidate_evidences_candidate_id ON product_candidate_evidences (candidate_id);
CREATE INDEX ix_product_candidate_evidences_evidence_type ON product_candidate_evidences (evidence_type);
CREATE INDEX ix_product_candidate_evidences_id ON product_candidate_evidences (id);
CREATE INDEX ix_product_candidate_evidences_recorded_at ON product_candidate_evidences (recorded_at);

CREATE TABLE product_candidate_decisions (
	id INTEGER NOT NULL,
	candidate_id INTEGER NOT NULL,
	action VARCHAR(20) NOT NULL,
	operator_id INTEGER NOT NULL,
	memo VARCHAR(1000),
	decided_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_product_candidate_decisions_candidate_id ON product_candidate_decisions (candidate_id);
CREATE INDEX ix_product_candidate_decisions_decided_at ON product_candidate_decisions (decided_at);
CREATE INDEX ix_product_candidate_decisions_id ON product_candidate_decisions (id);

COMMIT;

-- Rollback (수동 실행 전용, 이 파일 자체에서는 실행하지 않음):
-- 테이블 생성 역순으로 DROP한다.
--
-- BEGIN;
-- DROP INDEX IF EXISTS ix_product_candidate_decisions_id;
-- DROP INDEX IF EXISTS ix_product_candidate_decisions_decided_at;
-- DROP INDEX IF EXISTS ix_product_candidate_decisions_candidate_id;
-- DROP TABLE IF EXISTS product_candidate_decisions;
--
-- DROP INDEX IF EXISTS ix_product_candidate_evidences_recorded_at;
-- DROP INDEX IF EXISTS ix_product_candidate_evidences_id;
-- DROP INDEX IF EXISTS ix_product_candidate_evidences_evidence_type;
-- DROP INDEX IF EXISTS ix_product_candidate_evidences_candidate_id;
-- DROP TABLE IF EXISTS product_candidate_evidences;
--
-- DROP INDEX IF EXISTS ix_product_candidates_status;
-- DROP INDEX IF EXISTS ix_product_candidates_source_type;
-- DROP INDEX IF EXISTS ix_product_candidates_market;
-- DROP INDEX IF EXISTS ix_product_candidates_id;
-- DROP TABLE IF EXISTS product_candidates;
--
-- DROP INDEX IF EXISTS ix_execution_period_usages_scope_key;
-- DROP INDEX IF EXISTS ix_execution_period_usages_period_start;
-- DROP INDEX IF EXISTS ix_execution_period_usages_id;
-- DROP TABLE IF EXISTS execution_period_usages;
--
-- DROP INDEX IF EXISTS ix_execution_usages_product_id;
-- DROP INDEX IF EXISTS ix_execution_usages_occurred_at;
-- DROP INDEX IF EXISTS ix_execution_usages_id;
-- DROP TABLE IF EXISTS execution_usages;
--
-- DROP INDEX IF EXISTS ix_execution_limits_product_id;
-- DROP INDEX IF EXISTS ix_execution_limits_id;
-- DROP INDEX IF EXISTS ix_execution_limits_active;
-- DROP TABLE IF EXISTS execution_limits;
--
-- DROP INDEX IF EXISTS ix_emergency_stops_set_at;
-- DROP INDEX IF EXISTS ix_emergency_stops_is_active;
-- DROP INDEX IF EXISTS ix_emergency_stops_id;
-- DROP TABLE IF EXISTS emergency_stops;
--
-- DROP INDEX IF EXISTS ix_automation_mode_states_set_at;
-- DROP INDEX IF EXISTS ix_automation_mode_states_id;
-- DROP TABLE IF EXISTS automation_mode_states;
-- COMMIT;
