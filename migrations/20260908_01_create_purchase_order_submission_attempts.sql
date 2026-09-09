-- Purpose: V7 item 7(2026-09-08 후속, "확인된 발주 계약 구현") —
-- 실제 발주(POST seller/order/regist) 시도의 중복 실행 잠금과
-- 재시작 복구를 위한 신규 테이블(purchase_order_submission_attempts).
-- Source of Truth: app/domains/purchase_task/model.py,
-- app/domains/purchase_task/constants.py(OrderSubmissionStatus)
--
-- 개인정보(수취인명·연락처·주소)·비밀정보(JWT)는 이 테이블 어디에도
-- 없다 — product_code·options(id/qty)만 남긴다.
--
-- (company_id, idempotency_key) UNIQUE 제약이 곧 "동일 시도 중복
-- 실행 잠금"이다 — 애플리케이션 코드의 사전 조회가 아니라 DB
-- 제약으로 강제해 동시 요청 경쟁 상태까지 막는다.
--
-- 사용자 승인 범위(2026-09-08): 신규 Model·Migration 초안 작성과
-- 임시 DB 검증까지만 진행한다. 이 Migration은 실제 homez.db에
-- 적용하지 않는다 — 임시 SQLite 파일에서만 검증했다
-- (tests/test_purchase_order_submission_migration.py). 실제 발주·
-- 결제 실행 자체가 아직 별도 승인 대상이므로, 이 스키마를 실제
-- DB에 적용하는 것도 그 실행 승인과 함께 별도로 받는다.
--
-- 완전히 새로운 테이블(ALTER/재생성 없음) — 기존 테이블은 전혀
-- 건드리지 않는다.

BEGIN;

-- ====================================================
-- purchase_order_submission_attempts — 신규 테이블
-- ====================================================

CREATE TABLE purchase_order_submission_attempts (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	connection_id INTEGER NOT NULL,
	purchase_task_id INTEGER,
	idempotency_key VARCHAR(150) NOT NULL,
	mall_code VARCHAR(30) NOT NULL,
	product_code VARCHAR(100) NOT NULL,
	options_json VARCHAR(2000) NOT NULL,
	status VARCHAR(20) NOT NULL,
	external_order_code VARCHAR(200),
	failure_detail VARCHAR(500),
	triggered_by INTEGER,
	started_at DATETIME NOT NULL,
	finished_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_purchase_order_submission_attempts_company_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX ix_purchase_order_submission_attempts_id ON purchase_order_submission_attempts (id);
CREATE INDEX ix_purchase_order_submission_attempts_status ON purchase_order_submission_attempts (status);
CREATE INDEX ix_purchase_order_submission_attempts_connection_id ON purchase_order_submission_attempts (connection_id);
CREATE INDEX ix_purchase_order_submission_attempts_purchase_task_id ON purchase_order_submission_attempts (purchase_task_id);
CREATE INDEX ix_purchase_order_submission_attempts_company_id ON purchase_order_submission_attempts (company_id);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로
-- 실행. 완전히 새로운 테이블이라 DROP TABLE로 데이터 손실 없이
-- 되돌릴 수 있다 — 단, 이미 실제 발주 시도 이력이 쌓였다면 그
-- 이력 자체는 DROP TABLE로 사라진다):
-- BEGIN;
-- DROP TABLE purchase_order_submission_attempts;
-- COMMIT;
