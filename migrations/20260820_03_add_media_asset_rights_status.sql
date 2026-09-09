-- Purpose: 이미지 권리·상품 일치 검증(2026-08-20 3차 지시 — 최종
-- 정책) — media_assets에 rights_status(VERIFIED/RIGHTS_UNVERIFIED)
-- additive 컬럼을 추가한다. "일반 업로드=자동 VERIFIED"는 폐기됐다
-- — 모든 이미지(일반 업로드/긴 상세 이미지/검색)는 예외 없이
-- RIGHTS_UNVERIFIED로 시작하고, 사용자의 명시적 확인(근거 유형 필수,
-- confirm_rights_verified())을 거쳐야만 VERIFIED가 된다. 명시적 확인
-- 전에는 상품등록 마법사의 이미지 선택(listing_wizard_service.py::
-- update_media)에서 차단된다.
-- Source of Truth: app/domains/media_asset/model.py::MediaAsset
--
-- 2026-08-20 4차 지시 — 기존 행 소급 backfill 정책 재검토. 3차
-- 라운드에서는 "이미 운영 중이던 이미지를 소급 차단하면 안 된다"는
-- 이유로 기존 행 전체를 근거 없이 VERIFIED로 채웠다 — 이는 "VERIFIED
-- 전환은 오직 사용자 명시적 확인을 통해서만"이라는 이 라운드의
-- 원칙과 정면으로 어긋난다(근거 없는 소급 승인). 이번 Migration은
-- 그 결정을 되돌린다:
--   1) 신규·기존 행 구분 없이 전부 기본값 'RIGHTS_UNVERIFIED'로
--      시작한다(ADD COLUMN DEFAULT를 애플리케이션 기본값과 통일 —
--      더 이상 "1회성 예외"가 없다).
--   2) 유일한 예외는 "파생물"(asset_role='GENERATED')이면서
--      source_asset_id가 media_assets 자신의 다른 행을 실제로
--      가리키는 경우뿐이다 — 이 경우에만 원본 행의 rights_status를
--      그대로 복사한다(코드가 이미 강제하는 "파생물은 원본을 넘어설
--      수 없다" 규칙을 과거 데이터에도 동일하게 적용할 뿐이며, 이
--      시점 원본도 전부 RIGHTS_UNVERIFIED로 시작하므로 오늘 결과값은
--      원본과 동일하다 — 다만 리터럴 값을 박아 넣지 않고 실제
--      source_asset_id 관계를 SELECT로 따라가므로, 이후 어느 원본이
--      실제 사용자 확인을 거쳐 VERIFIED가 되어 이 UPDATE를 다시
--      돌리면 그 파생물도 정확히 같은 값을 따라가도록 구조가 맞춰져
--      있다).
--   3) 출처를 코드·데이터로 증명할 수 없는 행(대부분의 ORIGINAL,
--      source_asset_id가 NULL이거나 끊어진 행)은 예외 없이
--      RIGHTS_UNVERIFIED로 남는다 — "시스템 내부 생성으로 추정"
--      같은 근거 없는 분류를 만들지 않는다.
--   4) 이미 제출된 승인 스냅샷(approval_package_json 등)은 이
--      Migration이 전혀 건드리지 않는다 — media_assets.rights_status
--      한 컬럼만 변경한다.
-- Source of Truth: app/domains/media_asset/model.py::MediaAsset
--
-- 이 Migration은 실제 homez.db에 적용하지 않는다. 임시 SQLite
-- 파일에서만 검증했다(신규 빈 DB에 공식 Runner로 전체 28개 Migration을
-- 순서대로 적용해 integrity_check=ok, pending=[]까지 확인 완료 —
-- 2026-08-20 4차 라운드).

BEGIN;

ALTER TABLE media_assets ADD COLUMN rights_status VARCHAR(30) NOT NULL DEFAULT 'RIGHTS_UNVERIFIED';

UPDATE media_assets
SET rights_status = (
    SELECT src.rights_status
    FROM media_assets AS src
    WHERE src.id = media_assets.source_asset_id
)
WHERE asset_role = 'GENERATED'
  AND source_asset_id IS NOT NULL
  AND EXISTS (
    SELECT 1 FROM media_assets AS src WHERE src.id = media_assets.source_asset_id
  );

CREATE INDEX ix_media_assets_rights_status ON media_assets (rights_status);

COMMIT;

-- Rollback(주석 전용, 자동 실행되지 않음 — SQLite는 컬럼 DROP에
-- 테이블 재생성이 필요하다, 20260820_02와 동일한 제약).
