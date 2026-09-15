-- Purpose: 전면 감사(2026-09-14/15) Phase 2 — 승인-실행 결합 완성의
-- 첫 부분. 승인(PurchaseOrderApproval)이 상품코드는 이미 잠그지만
-- 옵션 구성(옵션 id·수량)은 잠그지 않아, 실행 직전 다른 옵션으로도
-- 통과할 수 있었다(독립 감사 IA-005의 잔여 부분).
-- Source of Truth: app/domains/purchase_task/model.py::PurchaseOrderApproval
--
-- purchase_order_approvals 신규 컬럼 1개(nullable, 기존 행에 영향
-- 없음 — 이 Migration 이전에 만들어진 승인 행은 옵션 재검증을
-- 생략한다, 추측으로 채우지 않는다).
--
-- 수취인 정보(이름·전화번호·주소)는 이 Migration에 포함하지 않는다
-- — 현재 화면 흐름상 수취인 정보는 승인 시점이 아니라 발주 제출
-- 시점에만 수집되므로, "승인 시점 수취인과 대조"할 원본 데이터
-- 자체가 아직 없다(감사 문서에 잔여 결함으로 별도 기록).
--
-- 승인 범위: 이 Migration 초안 작성과 임시 SQLite 파일 검증까지.
-- 실제 homez.db 적용은 별도 승인 후에만 진행한다.

BEGIN;

ALTER TABLE purchase_order_approvals ADD COLUMN options_snapshot_json TEXT;

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로
-- 실행. SQLite는 ALTER TABLE DROP COLUMN을 3.35.0+에서 지원한다):
-- BEGIN;
-- ALTER TABLE purchase_order_approvals DROP COLUMN options_snapshot_json;
-- COMMIT;
