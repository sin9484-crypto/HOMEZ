-- =========================================================
-- Homez OS
--
-- File : migrations/20260916_02_add_order_auto_collection_state_last_run_counts.sql
--
-- 2026-09-16 개인 베타 잔여 작업(Phase 7, HOMEZ_USER_OPERATION_SETTINGS.md
-- 2-8 운영 화면) — order_auto_collection_states에 마지막 실행의
-- 신규/중복/미연결/실패 주문수 4개 컬럼을 추가한다. 20260916_01의
-- 뒤를 잇는 순수 추가(ALTER ADD COLUMN)이며 기존 컬럼·데이터는
-- 전혀 건드리지 않는다.
-- =========================================================

BEGIN;

ALTER TABLE order_auto_collection_states ADD COLUMN last_new_fulfillment_count INTEGER;
ALTER TABLE order_auto_collection_states ADD COLUMN last_duplicate_fulfillment_count INTEGER;
ALTER TABLE order_auto_collection_states ADD COLUMN last_unresolved_item_count INTEGER;
ALTER TABLE order_auto_collection_states ADD COLUMN last_failed_order_count INTEGER;

COMMIT;

-- Rollback(수동, 실제 DB 적용 시 별도 승인 후에만 사용 — SQLite는
-- DROP COLUMN을 오래된 버전에서 지원하지 않으므로 테이블 재생성 필요):
-- 최신 SQLite(3.35+)라면:
-- ALTER TABLE order_auto_collection_states DROP COLUMN last_new_fulfillment_count;
-- ALTER TABLE order_auto_collection_states DROP COLUMN last_duplicate_fulfillment_count;
-- ALTER TABLE order_auto_collection_states DROP COLUMN last_unresolved_item_count;
-- ALTER TABLE order_auto_collection_states DROP COLUMN last_failed_order_count;
