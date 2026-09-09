-- HOMEZ V7 Coupang live submission — public image hosting cache (additive only).
-- vendorPath 전송을 위한 공개 URL 캐시. 원본 storage_path/파일은 그대로
-- 로컬에 남는다 — 이 컬럼은 "이미 업로드했는지, 어디로 업로드했는지"만
-- 기록한다.
ALTER TABLE media_assets
    ADD COLUMN public_url VARCHAR(1000);
ALTER TABLE media_assets
    ADD COLUMN public_url_provider VARCHAR(30);
ALTER TABLE media_assets
    ADD COLUMN public_url_uploaded_at DATETIME;
