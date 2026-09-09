-- Purpose: V7 SupplierOrderProvider 연결(2026-08-20, 2차 지시) —
-- "실제 발주는 승인 필요"가 Provider 호출 자체를 코드에서 빼는
-- 뜻이 아니라는 CTO 정정에 따라, 승인(Purchase.status=CONFIRMED)
-- 이후 실제 전송 결과를 기록할 컬럼을 purchases 테이블에 additive로
-- 추가한다.
-- Source of Truth: app/domains/purchase/model.py::Purchase
--
-- 기존 status/ALLOWED_TRANSITIONS(REQUESTED/CONFIRMED/RECEIVED/
-- CANCELLED)는 전혀 건드리지 않는다 — 전부 nullable 신규 컬럼만
-- 추가한다(기존 행은 전부 NULL, 기존 24개+20260820_00/01 Migration
-- checksum 무변경).
--
-- 이 Migration은 실제 homez.db에 적용하지 않는다. 임시 SQLite
-- 파일에서만 검증했다
-- (tests/test_source_supplier_product_links_migration.py 확장).

BEGIN;

ALTER TABLE purchases ADD COLUMN submission_status VARCHAR(30);
ALTER TABLE purchases ADD COLUMN submission_provider_code VARCHAR(30);
ALTER TABLE purchases ADD COLUMN supplier_order_id VARCHAR(150);
ALTER TABLE purchases ADD COLUMN submitted_at DATETIME;
ALTER TABLE purchases ADD COLUMN confirmed_price FLOAT;
ALTER TABLE purchases ADD COLUMN accepted_quantities_json VARCHAR(2000);
ALTER TABLE purchases ADD COLUMN rejected_quantities_json VARCHAR(2000);
ALTER TABLE purchases ADD COLUMN submission_error_code VARCHAR(100);
ALTER TABLE purchases ADD COLUMN submission_retryable BOOLEAN;
ALTER TABLE purchases ADD COLUMN submission_retry_after_seconds INTEGER;
ALTER TABLE purchases ADD COLUMN correlation_id VARCHAR(150);
ALTER TABLE purchases ADD COLUMN approval_fingerprint VARCHAR(64);

CREATE INDEX ix_purchases_submission_status ON purchases (submission_status);
CREATE INDEX ix_purchases_supplier_order_id ON purchases (supplier_order_id);

COMMIT;

-- Rollback(주석 전용, 자동 실행되지 않음 — SQLite는 컬럼 DROP에
-- 테이블 재생성이 필요하다):
-- SQLite ALTER TABLE은 DROP COLUMN을 직접 지원하지 않는다(3.35+는
-- 지원하나 이 프로젝트가 요구하는 최소 버전은 확인 전이므로, 재생성
-- 기법을 rollback 계획으로 남긴다):
-- CREATE TABLE purchases_old_20260820_02 AS SELECT
--   id, company_id, order_id, supplier_id, status, idempotency_key,
--   total_cost, supplier_order_number, memo, requested_at,
--   confirmed_at, received_at, cancelled_at, created_at, updated_at
--   FROM purchases;
-- DROP TABLE purchases;
-- ALTER TABLE purchases_old_20260820_02 RENAME TO purchases;
-- (인덱스/UNIQUE 제약 재생성 필요)
