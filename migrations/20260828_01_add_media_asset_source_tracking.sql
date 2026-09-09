-- HOMEZ V7 Image Studio Section 1 — 이미지 가져오기 출처 추적 (additive only).
-- URL로 가져온 이미지의 출처 URL/도메인과, 공급처/제조사/사용자촬영/
-- 미확인 구분을 기록한다. 로컬 파일 업로드는 source_url/source_domain이
-- 둘 다 NULL이다(출처 URL이 없다는 사실 자체가 정보 — 추정해 채우지
-- 않는다). source_classification 기본값은 UNKNOWN(SQLite는 컬럼 추가
-- 시 기존 행에도 DEFAULT를 그대로 적용한다).
ALTER TABLE media_assets
    ADD COLUMN source_url VARCHAR(2000);
ALTER TABLE media_assets
    ADD COLUMN source_domain VARCHAR(255);
ALTER TABLE media_assets
    ADD COLUMN source_classification VARCHAR(30) NOT NULL DEFAULT 'UNKNOWN';

CREATE INDEX ix_media_assets_source_domain ON media_assets (source_domain);
