-- =========================================================
-- Homez OS
--
-- File : migrations/20260915_01_add_purchase_order_submission_attempts_active_task_index.sql
--
-- 2026-09-15 전면 감사 후속(Phase 3, 업무 주문 단위 중복 방지 강화).
--
-- purchase_order_submission_attempts는 이미 (company_id,
-- idempotency_key) UNIQUE 제약을 갖고 있지만, 이것만으로는 "같은
-- purchase_task_id에 대해 서로 다른 idempotency_key(예: 옵션이 다른
-- 요청)로 거의 동시에 들어온 두 요청"까지는 막지 못한다.
-- order_submission_service.py의 _has_blocking_task_attempt()는
-- INSERT 이전에 실행하는 SELECT라, 두 프로세스(또는 같은 프로세스
-- 안에서도 네트워크 호출 대기 중 끼어든 경쟁 요청)가 둘 다 "아직
-- 없음"을 관측하고 통과하는 경쟁 상태(TOCTOU)가 이론상 가능하다.
--
-- 이 Migration은 그 틈을 DB 제약으로 직접 막는다 — 같은
-- (company_id, purchase_task_id)에 대해 "차단 대상" 상태(PENDING/
-- IN_FLIGHT/SUCCEEDED, 또는 RESULT_UNKNOWN이면서 아직
-- ORDER_NOT_CONFIRMED로 확정되지 않음)인 행은 동시에 하나만 존재할
-- 수 있도록 부분 UNIQUE INDEX를 추가한다. 이 조건은
-- _has_blocking_task_attempt()가 애플리케이션 레벨에서 판단하는
-- "차단 대상" 조건과 정확히 같다 — 애플리케이션 검사는 빠르고
-- 친절한 오류 메시지를 위한 사전 검사이고, 이 인덱스가 최종
-- 방어선이다.
--
-- 기존 컬럼·데이터는 전혀 건드리지 않는다 — 인덱스 추가만 한다.
-- =========================================================

BEGIN;

CREATE UNIQUE INDEX uq_purchase_order_submission_attempts_active_task
ON purchase_order_submission_attempts (company_id, purchase_task_id)
WHERE purchase_task_id IS NOT NULL AND (
    status IN ('PENDING', 'IN_FLIGHT', 'SUCCEEDED')
    OR (
        status = 'RESULT_UNKNOWN'
        AND unknown_resolution_status != 'ORDER_NOT_CONFIRMED'
    )
);

COMMIT;

-- Rollback(수동, 실제 DB 적용 시 별도 승인 후에만 사용):
-- DROP INDEX IF EXISTS uq_purchase_order_submission_attempts_active_task;
