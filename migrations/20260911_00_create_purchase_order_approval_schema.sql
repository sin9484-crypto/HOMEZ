-- Purpose: 반자동 완료 라운드(2026-09-11) Phase 5·7 — 실제 온채널
-- 발주 직전 "사용자 최종 승인"을 위한 신규 테이블(purchase_order_
-- approvals)과, 그 게이트가 참조하는 회사별 한도 설정 컬럼 2개
-- (purchase_task_policy_settings에 추가).
-- Source of Truth: app/domains/purchase_task/model.py
-- (PurchaseOrderApproval, PurchaseTaskPolicySetting), constants.py
-- (PurchaseOrderApprovalStatus, ShippingCostConfirmationSource,
-- RECOMMENDED_* 상수)
--
-- 배경: 온채널에 배송비를 사전 확정할 공식 API가 없다는 사실이
-- 확정됐다(docs/HOMEZ_ONCHANNEL_OPENAPI_FINDINGS_20260908.md "정정
-- (2026-09-11)" 절). 이 테이블은 사용자가 외부 화면에서 직접 확인한
-- 배송비를 근거(출처·메모·확인자·확인시각)와 함께 입력하는 반자동
-- 보완책 + 가격/포인트/한도/마진 최종 재확인 스냅샷을 한 행에
-- 기록한다. 개인정보는 이 테이블 어디에도 없다.
--
-- (company_id, connection_id, purchase_task_id) UNIQUE — 작업
-- 1건당 승인 1건만 "현재 상태"로 존재한다(append-only가 아니라
-- 현재상태 갱신형 — 배송비 재입력·재승인은 같은 행을 갱신한다).
--
-- purchase_task_policy_settings 신규 컬럼 2개(nullable — 기존 행에
-- 영향 없음): min_residual_points, order_approval_validity_minutes.
-- 둘 다 NULL이면 order_submission_service.py의 Gate E가 constants.py
-- 의 RECOMMENDED_* 상수(최소 잔여 포인트 100,000pt, 승인 유효시간
-- 10분)를 대신 쓴다 — 이 테이블의 다른 한도 컬럼(per_order_max_
-- amount 등)과 달리 "설정 안 함 = 무제한"으로 읽지 않는다(실제
-- 발주 직전 게이트이므로 안전한 시작값으로 읽는다).
--
-- 승인 범위: 이 Migration 초안 작성과 임시 SQLite 파일 검증까지.
-- 실제 homez.db 적용은 별도 승인 후에만 진행한다.

BEGIN;

-- ====================================================
-- purchase_order_approvals — 신규 테이블
-- ====================================================

CREATE TABLE purchase_order_approvals (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	connection_id INTEGER NOT NULL,
	purchase_task_id INTEGER NOT NULL,
	product_code VARCHAR(100) NOT NULL,
	status VARCHAR(30) NOT NULL,
	shipping_cost_amount INTEGER,
	shipping_cost_is_free_confirmed BOOLEAN NOT NULL,
	shipping_cost_source VARCHAR(40),
	shipping_cost_basis_memo VARCHAR(500),
	shipping_cost_confirmed_by INTEGER,
	shipping_cost_confirmed_at DATETIME,
	item_amount_snapshot INTEGER,
	required_points_snapshot INTEGER,
	current_points_snapshot INTEGER,
	margin_amount_snapshot INTEGER,
	margin_rate_snapshot FLOAT,
	approved_by INTEGER,
	approved_at DATETIME,
	expires_at DATETIME,
	invalidated_reason VARCHAR(200),
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_purchase_order_approvals_company_connection_task UNIQUE (company_id, connection_id, purchase_task_id)
);

CREATE INDEX ix_purchase_order_approvals_id ON purchase_order_approvals (id);
CREATE INDEX ix_purchase_order_approvals_status ON purchase_order_approvals (status);
CREATE INDEX ix_purchase_order_approvals_purchase_task_id ON purchase_order_approvals (purchase_task_id);
CREATE INDEX ix_purchase_order_approvals_company_id ON purchase_order_approvals (company_id);
CREATE INDEX ix_purchase_order_approvals_connection_id ON purchase_order_approvals (connection_id);

-- ====================================================
-- purchase_task_policy_settings — 컬럼 2개 추가
-- ====================================================

ALTER TABLE purchase_task_policy_settings ADD COLUMN min_residual_points INTEGER;
ALTER TABLE purchase_task_policy_settings ADD COLUMN order_approval_validity_minutes INTEGER;

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로
-- 실행. 새 테이블은 DROP TABLE로 데이터 손실 없이 되돌릴 수 있다
-- (단, 이미 실제 승인 이력이 쌓였다면 그 이력 자체는 사라진다).
-- SQLite는 ALTER TABLE DROP COLUMN을 3.35.0+에서 지원한다):
-- BEGIN;
-- DROP TABLE purchase_order_approvals;
-- ALTER TABLE purchase_task_policy_settings DROP COLUMN min_residual_points;
-- ALTER TABLE purchase_task_policy_settings DROP COLUMN order_approval_validity_minutes;
-- COMMIT;
