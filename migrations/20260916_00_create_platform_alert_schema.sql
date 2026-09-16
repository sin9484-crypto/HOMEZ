-- =========================================================
-- Homez OS
--
-- File : migrations/20260916_00_create_platform_alert_schema.sql
--
-- 2026-09-16 개인 베타 잔여 작업(Phase 5, HOMEZ_USER_OPERATION_SETTINGS.md
-- 10-18) — "서버 관리자" 전용 알림 채널. 기존 notification_center
-- 테이블(회사/사용자 스코프)과 완전히 분리된 플랫폼 전체 스코프
-- 테이블이다 — company_id가 없다(의도적).
--
-- platform_alert_recipients: 알림을 받을 연락처(회사 무관).
-- platform_alert_delivery_logs: 알림 생성→전달시도→성공/실패→
-- 재전송 이력. 감사로그(audit_logs)의 대체물이 아니다 — 별개 테이블.
--
-- 기존 테이블·데이터는 전혀 건드리지 않는다.
-- =========================================================

BEGIN;

CREATE TABLE platform_alert_recipients (
	id INTEGER NOT NULL,
	contact_type VARCHAR(10) NOT NULL,
	contact_value VARCHAR(200) NOT NULL,
	active BOOLEAN NOT NULL,
	label VARCHAR(100),
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE platform_alert_delivery_logs (
	id INTEGER NOT NULL,
	event_code VARCHAR(50) NOT NULL,
	severity VARCHAR(20) NOT NULL,
	title VARCHAR(200) NOT NULL,
	message VARCHAR(2000) NOT NULL,
	entity_ref VARCHAR(200),
	idempotency_key VARCHAR(150) NOT NULL,
	recipient_id INTEGER,
	channel VARCHAR(10),
	status VARCHAR(30) NOT NULL,
	attempt_count INTEGER NOT NULL,
	error_detail VARCHAR(500),
	next_retry_at DATETIME,
	created_at DATETIME NOT NULL,
	sent_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT uq_platform_alert_delivery_logs_key_recipient
		UNIQUE (idempotency_key, recipient_id)
);

CREATE INDEX ix_platform_alert_delivery_logs_event_code ON platform_alert_delivery_logs (event_code);
CREATE INDEX ix_platform_alert_delivery_logs_status ON platform_alert_delivery_logs (status);

COMMIT;

-- Rollback(수동, 실제 DB 적용 시 별도 승인 후에만 사용):
-- DROP TABLE IF EXISTS platform_alert_delivery_logs;
-- DROP TABLE IF EXISTS platform_alert_recipients;
