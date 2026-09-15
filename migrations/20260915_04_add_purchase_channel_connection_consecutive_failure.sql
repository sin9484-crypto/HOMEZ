-- =========================================================
-- Homez OS
--
-- File : migrations/20260915_04_add_purchase_channel_connection_consecutive_failure.sql
--
-- 2026-09-15 전면 감사 후속(Phase 9, HOMEZ_USER_OPERATION_SETTINGS.md
-- 8-16 — "매입처 조회 실패가 계속되면 사용자에게 알린다").
--
-- purchase_channel_connections에 consecutive_failure_count(NOT NULL,
-- 기본값 0) 컬럼을 추가한다. 실제 매입처 조회(lookup_product/
-- list_products 등)가 실패할 때마다 증가하고, 성공하면 0으로
-- 되돌린다 — 이 값이 임계치에 처음 도달하는 순간에만 사용자에게
-- 알림을 보낸다(매 실패마다 반복 알림을 보내지 않는다).
--
-- 기존 컬럼·데이터는 전혀 건드리지 않는다. 이 컬럼 추가 이전에
-- 생성된 기존 연결 행은 실제로 실패 이력을 추적한 적이 없었으므로
-- 기본값 0이 사실과 일치한다.
-- =========================================================

BEGIN;

ALTER TABLE purchase_channel_connections
ADD COLUMN consecutive_failure_count INTEGER NOT NULL DEFAULT 0;

COMMIT;

-- Rollback(수동, 실제 DB 적용 시 별도 승인 후에만 사용):
-- SQLite는 컬럼 DROP을 직접 지원하지 않는다 — 되돌리려면 테이블을
-- 재생성해야 한다(homez-migration-safety 원칙에 따라 자동 실행하지
-- 않음).
