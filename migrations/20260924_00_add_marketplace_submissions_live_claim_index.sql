-- =========================================================
-- Homez OS
--
-- File : migrations/20260924_00_add_marketplace_submissions_live_claim_index.sql
--
-- 2026-09-24 후속(자동 재신청 방지 라운드 3 — 중복 상품등록 차단).
-- 이번 라운드에서 설계·격리 리허설만 완료했다. 별도 승인 전에는
-- 실제 업무 DB(저장소 루트 homez.db)에 적용하지 않는다.
--
-- marketplace_submissions는 이미 (company_id, idempotency_key) UNIQUE
-- 제약을 갖고 있지만, idempotency_key는 위저드 단위로 계산되므로
-- (`wizard-{id}-account-{id}-submission`), 같은 상품 후보·같은
-- 판매계정으로 위저드를 새로 하나 더 만들면 새 idempotency_key로
-- listing_id는 같지만 완전히 다른 submission 행이 생긴다 — 이
-- UNIQUE로는 막지 못한다.
--
-- listing_wizard_live_service.py의 preflight()가 애플리케이션
-- 레벨에서 "같은 listing_id를 참조하는 다른 행 중 실제 전송을
-- 시도했고 아직 해소되지 않았거나 이미 성공한 것이 있는가"를
-- SELECT로 먼저 확인하지만, 이 검사는 INSERT/UPDATE 이전 SELECT라
-- 두 프로세스(또는 두 스레드)가 거의 동시에 통과하는 경쟁 상태
-- (TOCTOU)를 완전히 막지 못한다.
--
-- 이 Migration은 그 틈을 DB 제약으로 직접 막는다 — 같은
-- (company_id, listing_id)에 대해 "실제 전송을 시도한 적이 있고
-- (correlation_id IS NOT NULL — 구조 검증만 하는 submission_service.
-- submit()은 이 값을 절대 설정하지 않는다) 아직 해소되지 않았거나
-- (SUBMITTING/UNKNOWN) 이미 성공한(external_submission_ref 있음)"
-- 행은 동시에 하나만 존재할 수 있도록 부분 UNIQUE INDEX를 추가한다.
-- 실제로 명시적 거절(FAILED)로 끝난 행은 이 조건에서 제외된다 —
-- 정상적인 상품 수정 후 재등록 경로까지 막지 않기 위해서다.
--
-- 기존 컬럼·데이터는 전혀 건드리지 않는다 — 인덱스 추가만 한다.
-- =========================================================

BEGIN;

CREATE UNIQUE INDEX uq_marketplace_submissions_live_claim_per_listing
ON marketplace_submissions (company_id, listing_id)
WHERE correlation_id IS NOT NULL AND (
    external_submission_ref IS NOT NULL
    OR status IN ('SUBMITTING', 'UNKNOWN')
);

COMMIT;

-- Rollback(수동, 실제 DB 적용 시 별도 승인 후에만 사용):
-- DROP INDEX IF EXISTS uq_marketplace_submissions_live_claim_per_listing;
