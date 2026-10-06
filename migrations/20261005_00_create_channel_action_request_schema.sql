-- =========================================================
-- Homez OS
--
-- File : migrations/20261005_00_create_channel_action_request_schema.sql
--
-- 2026-10-05 판매채널 외부 변경(쿠팡 옵션 판매중지·고객 주문 취소)의 작업 장부 —
-- 신규 테이블 1개(추가형). 외부 요청을 보내기 **전에** (회사, 동작, 대상) 한 행을 먼저
-- 확정해 재시작·동시 실행·"외부 성공 후 내부 저장 실패"에서도 같은 요청을 다시 보내지
-- 않게 하고, 재시도 가능한 오류의 횟수·간격과 결과불명 상태를 영속화한다.
-- 기존 테이블·데이터는 전혀 건드리지 않는다. 비밀값·고객 개인정보 없음.
-- =========================================================

BEGIN;

CREATE TABLE channel_action_requests (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	store_connection_id INTEGER NOT NULL,
	action_type VARCHAR(30) NOT NULL,
	target_key VARCHAR(200) NOT NULL,
	status VARCHAR(20) NOT NULL,
	attempt_count INTEGER NOT NULL,
	next_retry_at DATETIME,
	last_http_status INTEGER,
	last_error_class VARCHAR(40),
	detail VARCHAR(300),
	triggered_by INTEGER,
	requested_at DATETIME NOT NULL,
	completed_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_channel_action_request_target UNIQUE (company_id, action_type, target_key)
);

CREATE INDEX ix_channel_action_requests_company_id ON channel_action_requests (company_id);
CREATE INDEX ix_channel_action_requests_id ON channel_action_requests (id);
CREATE INDEX ix_channel_action_requests_status ON channel_action_requests (status);
CREATE INDEX ix_channel_action_requests_store_connection_id ON channel_action_requests (store_connection_id);

COMMIT;

-- Rollback(수동, 실제 DB 적용 시 별도 승인 후에만 사용):
-- DROP TABLE IF EXISTS channel_action_requests;
