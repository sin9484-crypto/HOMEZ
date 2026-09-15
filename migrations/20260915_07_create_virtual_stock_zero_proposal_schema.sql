-- =========================================================
-- Homez OS
--
-- File : migrations/20260915_07_create_virtual_stock_zero_proposal_schema.sql
--
-- 2026-09-15 전면 감사 후속(Phase 9F, HOMEZ_USER_OPERATION_SETTINGS.md
-- 8-19 — "매입처의 판매 가능 여부를 확인할 수 없으면 가상재고를
-- 0으로 바꾸고 신규 판매를 중지한다").
--
-- virtual_stock_zero_proposals: 공급처 판매 가능 여부를 확인할 수
-- 없을 때 생성되는 "가상재고 0 제안" 현재 상태 1행
-- (PENDING→APPROVED/REJECTED). 외부 판매채널에 자동으로 전송하지
-- 않는다 — 이 테이블은 제안·승인 이력만 담는다. 사람의 승인 없이는
-- 스스로 상태가 바뀌지 않는다(재고 확인이 나중에 성공해도 자동
-- 복구 없음).
--
-- 기존 테이블·데이터는 전혀 건드리지 않는다.
-- =========================================================

BEGIN;

CREATE TABLE virtual_stock_zero_proposals (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	connection_id INTEGER NOT NULL,
	product_code VARCHAR(100) NOT NULL,
	status VARCHAR(20) NOT NULL,
	reason VARCHAR(500) NOT NULL,
	created_at DATETIME NOT NULL,
	resolved_by INTEGER,
	resolved_at DATETIME,
	resolution_note VARCHAR(500),
	PRIMARY KEY (id)
);

CREATE INDEX ix_virtual_stock_zero_proposals_id ON virtual_stock_zero_proposals (id);
CREATE INDEX ix_virtual_stock_zero_proposals_company_id ON virtual_stock_zero_proposals (company_id);
CREATE INDEX ix_virtual_stock_zero_proposals_connection_id ON virtual_stock_zero_proposals (connection_id);
CREATE INDEX ix_virtual_stock_zero_proposals_product_code ON virtual_stock_zero_proposals (product_code);
CREATE INDEX ix_virtual_stock_zero_proposals_status ON virtual_stock_zero_proposals (status);

COMMIT;

-- Rollback(수동, 실제 DB 적용 시 별도 승인 후에만 사용):
-- DROP TABLE IF EXISTS virtual_stock_zero_proposals;
