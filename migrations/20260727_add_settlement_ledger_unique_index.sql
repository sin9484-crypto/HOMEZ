-- Purpose: Marketplace Settlement 전용 부분 유일 인덱스.
-- funding_ledgers(reference_id, type)에 대해 reference_type='settlement'인
-- 행에만 적용되어, 동시 confirm_deposit/reverse 요청의 중복 Ledger 생성을
-- DB 레벨에서 차단한다. 다른 reference_type(order/account/purchase 등)의
-- Ledger에는 영향을 주지 않는다.
--
-- 적용 전 필수: 아래 쿼리로 기존 중복 데이터가 없는지 먼저 확인할 것.
-- 중복이 있으면 이 CREATE UNIQUE INDEX 문은 즉시 실패한다.
--
-- SELECT reference_id, type, COUNT(*) AS cnt
-- FROM funding_ledgers
-- WHERE reference_type = 'settlement'
-- GROUP BY reference_id, type
-- HAVING COUNT(*) > 1;

CREATE UNIQUE INDEX IF NOT EXISTS uq_funding_ledger_settlement_type
ON funding_ledgers (reference_id, type)
WHERE reference_type = 'settlement';

-- Rollback (수동 실행 전용, 이 파일 자체에서는 실행하지 않음):
-- DROP INDEX IF EXISTS uq_funding_ledger_settlement_type;
