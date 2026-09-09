-- Purpose: 중앙 운영 승인·예외 알림 전달 엔진(Gate PT-3, 2026-08-23
-- 17차 지시) — event_catalog.py 이벤트의 이메일 발송 이력/재시도
-- 상태와 사용자별 알림 설정.
-- Source of Truth: app/domains/notification_center/model.py
--
-- 3개 신규 테이블:
--   notification_email_logs: 중앙 카탈로그 이벤트의 이메일 발송
--     이력(append-only + 재시도 상태 추적). 기존 notifications/
--     notification_reads(Gate Y-3, 앱 내부 알림 전용)나
--     purchase_task_email_logs(purchase_task 자체 20개 이벤트 전용,
--     별개 시스템)와 겹치지 않는다 — 멱등키는
--     UNIQUE(company_id, idempotency_key)로 보장한다.
--   notification_preferences: 회사+사용자 전역 알림 설정(이메일
--     on/off, 조용한 시간) — 회사당 사용자 1행.
--   notification_event_preferences: 이벤트별 override — 행이 없으면
--     event_catalog.py 기본값을 그대로 따른다.
--
-- 이 Migration은 실제 homez.db(개발·운영 모두)에 적용하지 않는다.
-- 임시 SQLite 파일에서만 검증했다(tests/test_notification_delivery_pt3.py).
-- 적용 전 실제 DB 두 곳(개발 루트/운영 데이터 디렉터리) 읽기 전용
-- 기준선 확인 완료 — 두 파일 모두 PRAGMA integrity_check=ok,
-- foreign_key_check 0건, 이 Migration이 만들 3개 테이블 아직 없음.

BEGIN;

CREATE TABLE notification_email_logs (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	event_code VARCHAR(50) NOT NULL,
	entity_ref VARCHAR(200),
	locale VARCHAR(10) NOT NULL,
	status VARCHAR(30) NOT NULL,
	idempotency_key VARCHAR(150) NOT NULL,
	attempt_count INTEGER NOT NULL,
	error_detail VARCHAR(500),
	next_retry_at DATETIME,
	created_at DATETIME NOT NULL,
	sent_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT uq_notification_email_logs_company_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX ix_notification_email_logs_status ON notification_email_logs (status);
CREATE INDEX ix_notification_email_logs_event_code ON notification_email_logs (event_code);
CREATE INDEX ix_notification_email_logs_user_id ON notification_email_logs (user_id);
CREATE INDEX ix_notification_email_logs_company_id ON notification_email_logs (company_id);

CREATE TABLE notification_preferences (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	email_enabled BOOLEAN NOT NULL,
	quiet_hours_start INTEGER,
	quiet_hours_end INTEGER,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_notification_preferences_company_user UNIQUE (company_id, user_id)
);

CREATE INDEX ix_notification_preferences_company_id ON notification_preferences (company_id);
CREATE INDEX ix_notification_preferences_user_id ON notification_preferences (user_id);

CREATE TABLE notification_event_preferences (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	event_code VARCHAR(50) NOT NULL,
	enabled BOOLEAN NOT NULL,
	email_immediate_override BOOLEAN,
	unconfirmed_wait_minutes_override INTEGER,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_notification_event_preferences_company_user_event UNIQUE (company_id, user_id, event_code)
);

CREATE INDEX ix_notification_event_preferences_event_code ON notification_event_preferences (event_code);
CREATE INDEX ix_notification_event_preferences_company_id ON notification_event_preferences (company_id);
CREATE INDEX ix_notification_event_preferences_user_id ON notification_event_preferences (user_id);

COMMIT;

-- Rollback (주석 — 자동 실행 안 됨):
-- BEGIN;
-- DROP INDEX ix_notification_event_preferences_user_id;
-- DROP INDEX ix_notification_event_preferences_company_id;
-- DROP INDEX ix_notification_event_preferences_event_code;
-- DROP TABLE notification_event_preferences;
-- DROP INDEX ix_notification_preferences_user_id;
-- DROP INDEX ix_notification_preferences_company_id;
-- DROP TABLE notification_preferences;
-- DROP INDEX ix_notification_email_logs_company_id;
-- DROP INDEX ix_notification_email_logs_user_id;
-- DROP INDEX ix_notification_email_logs_event_code;
-- DROP INDEX ix_notification_email_logs_status;
-- DROP TABLE notification_email_logs;
-- COMMIT;
