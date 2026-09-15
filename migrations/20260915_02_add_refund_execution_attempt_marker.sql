-- =========================================================
-- Homez OS
--
-- File : migrations/20260915_02_add_refund_execution_attempt_marker.sql
--
-- 2026-09-15 전면 감사 후속(Phase 4, IA-011 — 결제·환불 Provider
-- 연결 전 트랜잭션 설계 공백).
--
-- 기존 RefundService.mark_executed()는 외부 Executor 호출(현재는
-- FakeRefundExecutor뿐)을 먼저 실행하고, 그 뒤에 로컬 상태 전이를
-- commit했다. 실행이 실제로 외부에 도달한 뒤 commit 이전에
-- 프로세스가 중단되면, 외부 실행은 성공했는데 로컬 상태는 여전히
-- APPROVED로 남아 재시도 시 Executor가 다시 호출되는(이중 환불)
-- 위험이 구조적으로 존재했다.
--
-- 이 Migration은 refunds에 execution_attempt_started_at(nullable)을
-- 추가한다 — mark_executed()가 Executor를 호출하기 "직전"에 이
-- 값을 채우고 commit해, 외부 호출 시도 자체를 durable하게 먼저
-- 남긴다. status가 여전히 APPROVED인데 이 값이 채워져 있으면
-- "직전 시도가 확정되지 못했다"는 뜻이므로, 서비스 레벨에서 사람의
-- 명시적 확인 없이는 재시도하지 않는다.
--
-- 기존 컬럼·데이터는 전혀 건드리지 않는다.
-- =========================================================

BEGIN;

ALTER TABLE refunds ADD COLUMN execution_attempt_started_at DATETIME;

COMMIT;

-- Rollback(수동, 실제 DB 적용 시 별도 승인 후에만 사용):
-- SQLite는 컬럼 DROP을 직접 지원하지 않는다 — 되돌리려면 테이블을
-- 재생성해야 한다(homez-migration-safety 원칙에 따라 자동 실행하지
-- 않음).
