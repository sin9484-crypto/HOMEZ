-- Purpose: HOMEZ 사용자 운영 기준(HOMEZ_USER_OPERATION_SETTINGS.md)
-- 8-2·10번 — "가격이 기존 확인값보다 인상됐으면 자동발주를 중지한다"
-- (Phase 10 재작업). Phase 4(2026-09-09)에서 이 판정 코드
-- (`PurchaseTaskPolicyReason.PRICE_INCREASE_RATE_EXCEEDED`)는
-- 이미 만들었지만, 그 판정에 필요한 "이전에 확인한 가격" 기준값을
-- 실제로 채우는 호출부·컬럼이 저장소 어디에도 없어 이 판정이 항상
-- None 처리로 건너뛰어졌다(Critical 결함 #21로 기록됨). 이 Migration은
-- 그 기준값을 실제로 저장할 컬럼을 추가한다.
-- Source of Truth: app/domains/purchase_task/model.py
--                   (PurchaseTaskCandidate.expected_amount_at_creation)
--
-- purchase_task_candidates 테이블에 컬럼 1개를 추가한다(nullable —
-- 기존 행에 영향 없음):
--   expected_amount_at_creation : 이 후보가 처음 평가돼 실질 매입비
--                                 (required_budget_amount)가 처음
--                                 계산된 시점의 값. 그 이후
--                                 재평가(evaluate_and_prepare 재호출)
--                                 때마다 이 값과 비교해 가격 인상률을
--                                 판정한다 — app/domains/purchase_task/
--                                 service.py::evaluate_and_prepare()가
--                                 "이 컬럼이 아직 NULL일 때만" 채운다
--                                 (한 번 채워지면 그 후보의 평가
--                                 이력 내내 불변 기준선).
--
-- 완전히 새로운 컬럼 추가만 있고(ALTER TABLE ADD COLUMN), 기존
-- 컬럼·데이터는 전혀 건드리지 않는다.
--
-- 승인 범위: 이 Migration 초안 작성과 임시 SQLite 파일 검증까지.
-- 실제 homez.db 적용은 별도 승인 후에만 진행한다.

BEGIN;

ALTER TABLE purchase_task_candidates ADD COLUMN expected_amount_at_creation FLOAT;

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로
-- 실행. SQLite는 ALTER TABLE DROP COLUMN을 3.35.0+에서 지원한다.
-- 컬럼을 드롭하면 그 시점까지 기록된 가격 기준선이 사라져 가격
-- 인상 감지가 다시 항상 비활성 상태로 돌아간다 — 후보 조회·평가
-- 자체의 가용성에는 영향 없다):
-- BEGIN;
-- ALTER TABLE purchase_task_candidates DROP COLUMN expected_amount_at_creation;
-- COMMIT;
