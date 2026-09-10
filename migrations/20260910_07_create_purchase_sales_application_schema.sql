-- Purpose: 온채널 공식 답변(2026-09-10, 사용자 문의) — "발주 전
-- 판매신청이 필수"가 확정됨에 따라, 발주(order/regist) 이전에 반드시
-- 통과해야 하는 판매신청(product/apply) 접수 상태를 추적하는 신규
-- 테이블(purchase_sales_application_attempts).
-- Source of Truth: app/domains/purchase_task/model.py
-- (PurchaseSalesApplicationAttempt), app/domains/purchase_task/
-- constants.py(SalesApplicationStatus)
--
-- 개인정보·비밀정보(JWT)는 이 테이블 어디에도 없다 — product_code·
-- 접수 상태만 남긴다.
--
-- purchase_order_submission_attempts와 달리 (company_id,
-- connection_id, product_code) UNIQUE다 — idempotency_key가 아니다.
-- 판매신청은 금전·중복 위험이 없어(요청 바디에 결제·금액 필드가
-- 없다) 실패한 시도를 같은 행 위에서 재시도할 수 있어야 하기
-- 때문이다(model.py의 클래스 docstring 참고).
--
-- 사용자 승인 범위(2026-09-10): 신규 Model·Migration 초안 작성과
-- 임시 DB 검증까지만 진행한다. 이 Migration은 실제 homez.db에
-- 적용하지 않는다 — 임시 SQLite 파일에서만 검증했다
-- (tests/test_purchase_sales_application_migration.py). 실제 판매
-- 신청·발주 실행 자체가 아직 별도 승인 대상이므로, 이 스키마를
-- 실제 DB에 적용하는 것도 그 실행 승인과 함께 별도로 받는다.
--
-- 완전히 새로운 테이블(ALTER/재생성 없음) — 기존 테이블은 전혀
-- 건드리지 않는다.

BEGIN;

-- ====================================================
-- purchase_sales_application_attempts — 신규 테이블
-- ====================================================

CREATE TABLE purchase_sales_application_attempts (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	connection_id INTEGER NOT NULL,
	mall_code VARCHAR(30) NOT NULL,
	product_code VARCHAR(100) NOT NULL,
	status VARCHAR(20) NOT NULL,
	applied_product_code VARCHAR(100),
	failure_detail VARCHAR(500),
	triggered_by INTEGER,
	started_at DATETIME NOT NULL,
	finished_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_purchase_sales_application_attempts_company_connection_product UNIQUE (company_id, connection_id, product_code)
);

CREATE INDEX ix_purchase_sales_application_attempts_connection_id ON purchase_sales_application_attempts (connection_id);
CREATE INDEX ix_purchase_sales_application_attempts_company_id ON purchase_sales_application_attempts (company_id);
CREATE INDEX ix_purchase_sales_application_attempts_id ON purchase_sales_application_attempts (id);
CREATE INDEX ix_purchase_sales_application_attempts_status ON purchase_sales_application_attempts (status);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로
-- 실행. 완전히 새로운 테이블이라 DROP TABLE로 데이터 손실 없이
-- 되돌릴 수 있다 — 단, 이미 실제 판매신청 시도 이력이 쌓였다면 그
-- 이력 자체는 DROP TABLE로 사라진다):
-- BEGIN;
-- DROP TABLE purchase_sales_application_attempts;
-- COMMIT;
