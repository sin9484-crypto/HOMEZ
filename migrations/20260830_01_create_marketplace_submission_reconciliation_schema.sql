-- Purpose: HOMEZ V7 후속 안정화 Phase 5 — 성공 제출 정합화
-- (marketplace_submission_reconciliations) 생성.
-- Source of Truth: app/domains/marketplace_listing/model.py
--
-- 대상 1개 테이블 (FK 없음 — company_id/submission_id/
-- reconciled_by_user_id는 논리 참조 컬럼):
--   marketplace_submission_reconciliations
--
-- 목적: marketplace_submissions는 append-only 감사 기록이라(모델
-- docstring 참고) 실제로 성공한 제출이 파싱 버그 등으로 UNKNOWN/
-- FAILED로 잘못 기록됐어도 그 행을 직접 UPDATE하지 않는다. 이
-- 테이블은 "이 submission_id는 나중에 이런 근거(운영자가 직접
-- 확인한 sellerProductId, 상태 조회 결과, 시각·사유)로 정합화됐다"는
-- 사실만 별도 행으로 추가한다 — 원본 행은 그대로 보존된다.
--
-- 이 Migration은 실제 homez.db(개발·운영 모두)에 적용하지 않는다.
-- 임시 DB에서만 검증한다.
--
-- 실행 전 필수 확인: marketplace_submission_reconciliations 테이블이
-- 대상 DB에 존재하지 않아야 한다(IF NOT EXISTS를 쓰지 않음 — 부분
-- 적용 상태를 그대로 실패시킨다).

BEGIN;

CREATE TABLE marketplace_submission_reconciliations (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	submission_id INTEGER NOT NULL,
	external_submission_ref VARCHAR(200) NOT NULL,
	observed_status_name VARCHAR(50),
	reconciled_by_user_id INTEGER NOT NULL,
	reconciled_at DATETIME NOT NULL,
	reason VARCHAR(500) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_marketplace_submission_reconciliations_submission_id UNIQUE (submission_id)
);

CREATE INDEX ix_marketplace_submission_reconciliations_company_id ON marketplace_submission_reconciliations (company_id);
CREATE INDEX ix_marketplace_submission_reconciliations_submission_id ON marketplace_submission_reconciliations (submission_id);
CREATE INDEX ix_marketplace_submission_reconciliations_id ON marketplace_submission_reconciliations (id);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로 실행):
-- BEGIN;
-- DROP INDEX ix_marketplace_submission_reconciliations_id;
-- DROP INDEX ix_marketplace_submission_reconciliations_submission_id;
-- DROP INDEX ix_marketplace_submission_reconciliations_company_id;
-- DROP TABLE marketplace_submission_reconciliations;
-- COMMIT;
