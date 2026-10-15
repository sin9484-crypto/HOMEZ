-- =========================================================
-- Homez OS
--
-- File : migrations/20261016_00_create_external_call_allowance_schema.sql
--
-- 2026-10-16 바깥 호출(쿠팡·온채널) 허용과 호출 기록 — 신규 테이블 2개(추가형).
--   external_call_allowances : 허용 요청(PENDING) → 허용 중(ACTIVE) → 소진/만료/취소 전 과정을 한 행으로
--   external_call_logs       : 바깥 호출을 보낸(또는 기록만 한) 내역
-- 기존 테이블·데이터는 전혀 건드리지 않는다. 비밀값·고객 개인정보 없음.
-- =========================================================

BEGIN;

CREATE TABLE external_call_allowances (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	system VARCHAR(20) NOT NULL,
	kind VARCHAR(10) NOT NULL,
	purpose VARCHAR(40) NOT NULL,
	mode VARCHAR(10) NOT NULL,
	status VARCHAR(12) NOT NULL,
	requested_count INTEGER,
	remaining_count INTEGER,
	per_hour_limit INTEGER,
	expires_at DATETIME,
	reason VARCHAR(300),
	requested_by INTEGER,
	decided_by INTEGER,
	decided_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_external_call_allowances_company_id ON external_call_allowances (company_id);

CREATE INDEX ix_external_call_allowances_id ON external_call_allowances (id);

CREATE INDEX ix_external_call_allowances_status ON external_call_allowances (status);

CREATE TABLE external_call_logs (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	system VARCHAR(20) NOT NULL,
	kind VARCHAR(10) NOT NULL,
	method VARCHAR(8),
	purpose VARCHAR(40) NOT NULL,
	target VARCHAR(120),
	outcome VARCHAR(12) NOT NULL,
	http_status INTEGER,
	allowance_id INTEGER,
	actor_user_id INTEGER,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_external_call_logs_allowance_id ON external_call_logs (allowance_id);

CREATE INDEX ix_external_call_logs_company_id ON external_call_logs (company_id);

CREATE INDEX ix_external_call_logs_created_at ON external_call_logs (created_at);

CREATE INDEX ix_external_call_logs_id ON external_call_logs (id);

COMMIT;

-- Rollback(수동, 실제 DB 적용 시 별도 승인 후에만 사용):
-- DROP TABLE IF EXISTS external_call_logs;
-- DROP TABLE IF EXISTS external_call_allowances;
