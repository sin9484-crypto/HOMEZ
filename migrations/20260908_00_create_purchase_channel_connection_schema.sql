-- Purpose: V7 item 7(2026-09-08) "사용자 계정 기반 매입 연결" —
-- 회사가 연결한 매입처 계정 카탈로그(purchase_channel_connections)와
-- 그 감사 로그(purchase_channel_connection_events)를 신설하고,
-- 기존 purchase_tasks/purchase_records에 어느 연결로 처리(예정/실제)
-- 됐는지 논리 참조 컬럼을 추가한다.
-- Source of Truth: app/domains/purchase_task/model.py,
-- app/domains/purchase_task/constants.py
--
-- 비밀번호·쿠키·카드정보 원문은 이 Migration 어디에도 없다.
-- credential_reference는 Windows Credential Manager의 target name일
-- 뿐이다(app/core/windows_credential_store.py 참고). account_label은
-- 표시용일 뿐이라 UNIQUE 대상이 아니다 — 식별은 항상 id로 한다.
--
-- 사용자 승인 범위(2026-09-08): 신규 Model·Migration 초안 작성과
-- 임시 DB 검증까지만 진행한다. 이 Migration은 실제 homez.db에
-- 적용하지 않는다 — 임시 SQLite 파일(기존 Migration을 순서대로
-- 적용해 재현한 스키마)에서만 검증했다
-- (tests/test_purchase_channel_connection_migration.py).
--
-- purchase_channel_connections/purchase_channel_connection_events는
-- 완전히 새로운 테이블(ALTER/재생성 없음). purchase_tasks/
-- purchase_records에 대한 변경은 단순 ADD COLUMN이며 기존 컬럼을
-- 건드리지 않는다.

BEGIN;

-- ====================================================
-- 1) purchase_channel_connections — 신규 테이블
-- ====================================================

CREATE TABLE purchase_channel_connections (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	mall_code VARCHAR(30) NOT NULL,
	connection_method VARCHAR(20) NOT NULL,
	account_label VARCHAR(200) NOT NULL,
	credential_reference VARCHAR(200),
	browser_profile_reference VARCHAR(200),
	status VARCHAR(30) NOT NULL,
	verified_at DATETIME,
	last_checked_at DATETIME,
	is_active BOOLEAN NOT NULL,
	disconnected_at DATETIME,
	memo VARCHAR(500),
	idempotency_key VARCHAR(150),
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_purchase_channel_connections_company_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX ix_purchase_channel_connections_status ON purchase_channel_connections (status);
CREATE INDEX ix_purchase_channel_connections_company_id ON purchase_channel_connections (company_id);
CREATE INDEX ix_purchase_channel_connections_id ON purchase_channel_connections (id);
CREATE INDEX ix_purchase_channel_connections_is_active ON purchase_channel_connections (is_active);
CREATE INDEX ix_purchase_channel_connections_mall_code ON purchase_channel_connections (mall_code);

-- ====================================================
-- 2) purchase_channel_connection_events — 신규 테이블(append-only
--    감사 로그)
-- ====================================================

CREATE TABLE purchase_channel_connection_events (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	connection_id INTEGER NOT NULL,
	event_type VARCHAR(30) NOT NULL,
	detail VARCHAR(500),
	triggered_by INTEGER,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_purchase_channel_connection_events_company_id ON purchase_channel_connection_events (company_id);
CREATE INDEX ix_purchase_channel_connection_events_connection_id ON purchase_channel_connection_events (connection_id);
CREATE INDEX ix_purchase_channel_connection_events_id ON purchase_channel_connection_events (id);

-- ====================================================
-- 3) purchase_tasks — channel_connection_id 추가(이 작업이 배정된
--    연결, 단순 ADD COLUMN)
-- ====================================================

ALTER TABLE purchase_tasks ADD COLUMN channel_connection_id INTEGER;

CREATE INDEX ix_purchase_tasks_channel_connection_id ON purchase_tasks (channel_connection_id);

-- ====================================================
-- 4) purchase_records — channel_connection_id 추가(실제 이 구매에
--    쓰인 연결, 단순 ADD COLUMN — 연결 해제 후에도 이 값은 절대
--    바꾸지 않는다는 것이 app/domains/purchase_task/model.py의 계약)
-- ====================================================

ALTER TABLE purchase_records ADD COLUMN channel_connection_id INTEGER;

CREATE INDEX ix_purchase_records_channel_connection_id ON purchase_records (channel_connection_id);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로
-- 실행. 이 Migration은 전부 additive라 데이터 손실 없이 되돌릴 수
-- 있다 — 단, purchase_channel_connections/_events에 이미 실제 행이
-- 쌓였다면 DROP TABLE로 그 데이터 자체는 사라진다):
-- BEGIN;
-- DROP INDEX ix_purchase_records_channel_connection_id;
-- ALTER TABLE purchase_records DROP COLUMN channel_connection_id;
-- DROP INDEX ix_purchase_tasks_channel_connection_id;
-- ALTER TABLE purchase_tasks DROP COLUMN channel_connection_id;
-- DROP TABLE purchase_channel_connection_events;
-- DROP TABLE purchase_channel_connections;
-- COMMIT;
