-- HOMEZ V7 후속 안정화 — 성공 경고 보존(additive only).
-- 2026-08-30. 아직 개발/운영 DB 어디에도 적용하지 않는다(초안만
-- 작성 — Model(app/domains/marketplace_listing/model.py::
-- MarketplaceSubmission)이 이미 이 컬럼들을 선언하고 있으며, 이
-- 파일은 그 Model에서 파생된 DDL이다). outcome=SUBMITTED(성공)여도
-- 쿠팡이 함께 돌려준 확인 필요 안내(예: Brand Enrollment 경고)를
-- 기존 error_reason과 절대 섞지 않고 별도 컬럼에 보존한다.
ALTER TABLE marketplace_submissions
    ADD COLUMN provider_warning_summary VARCHAR(1000);
ALTER TABLE marketplace_submissions
    ADD COLUMN provider_response_code VARCHAR(80);
