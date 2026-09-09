-- Purpose: HOMEZ AI 상품 초안·이미지 통합(Phase 2) — Media Asset +
-- Listing Package 전체 스키마 생성.
-- Source of Truth: app/domains/media_asset/model.py,
--                   app/domains/listing_package/model.py
--
-- 대상 6개 테이블 (Model 기준, ForeignKey 없음 — 전부 논리 참조 컬럼):
--   media_assets
--   image_generation_jobs
--   image_generation_results
--   image_generation_daily_usage
--   listing_packages
--   listing_package_approvals
--
-- 2026-08-02 Phase 2 신규 — company_id는 이 도메인의 모든 테이블에
-- 처음부터 필수(전역 테이블 없음, MarketplaceListing tenant 격리
-- 재감사와 동일한 원칙을 나중에 보완하지 않고 설계 시점부터 적용).
--
-- 이 Migration은 실제 homez.db에 적용하지 않는다. 임시 DB에서만
-- 검증한다(Phase 2 CTO 승인 지시: "실제 DB Migration 적용 직전에
-- 멈춰라").
--
-- 실행 전 필수 확인: 아래 6개 테이블이 대상 DB에 하나도 존재하지
-- 않아야 한다. 일부만 존재하는 상태(부분 적용)를 이 스크립트는
-- 감지하지 않고 그대로 CREATE TABLE을 시도해 실패하도록 둔다
-- (IF NOT EXISTS를 쓰지 않음).

BEGIN;

CREATE TABLE media_assets (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	owner_type VARCHAR(30) NOT NULL,
	owner_id INTEGER NOT NULL,
	asset_role VARCHAR(30) NOT NULL,
	source_asset_id INTEGER,
	channel_code VARCHAR(30),
	purpose VARCHAR(30) NOT NULL,
	display_order INTEGER NOT NULL,
	storage_path VARCHAR(500) NOT NULL,
	original_filename VARCHAR(255),
	mime_type VARCHAR(50) NOT NULL,
	file_size_bytes INTEGER NOT NULL,
	sha256_hex VARCHAR(64) NOT NULL,
	width INTEGER,
	height INTEGER,
	status VARCHAR(20) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_media_assets_company_storage_path UNIQUE (company_id, storage_path)
);

CREATE INDEX ix_media_assets_asset_role ON media_assets (asset_role);
CREATE INDEX ix_media_assets_company_id ON media_assets (company_id);
CREATE INDEX ix_media_assets_id ON media_assets (id);
CREATE INDEX ix_media_assets_owner_id ON media_assets (owner_id);
CREATE INDEX ix_media_assets_owner_type ON media_assets (owner_type);
CREATE INDEX ix_media_assets_purpose ON media_assets (purpose);
CREATE INDEX ix_media_assets_sha256_hex ON media_assets (sha256_hex);
CREATE INDEX ix_media_assets_source_asset_id ON media_assets (source_asset_id);
CREATE INDEX ix_media_assets_status ON media_assets (status);

CREATE TABLE image_generation_jobs (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	listing_package_id INTEGER,
	product_candidate_id INTEGER NOT NULL,
	provider_code VARCHAR(30) NOT NULL,
	model_name VARCHAR(100),
	prompt_fingerprint VARCHAR(64) NOT NULL,
	request_payload_json VARCHAR(4000) NOT NULL,
	status VARCHAR(20) NOT NULL,
	progress_percent INTEGER NOT NULL,
	retry_count INTEGER NOT NULL,
	max_retries INTEGER NOT NULL,
	estimated_cost NUMERIC(12, 4),
	actual_cost NUMERIC(12, 4),
	error_reason VARCHAR(1000),
	idempotency_key VARCHAR(160) NOT NULL,
	requested_by INTEGER NOT NULL,
	requested_at DATETIME NOT NULL,
	started_at DATETIME,
	completed_at DATETIME,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_image_generation_jobs_company_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX ix_image_generation_jobs_company_id ON image_generation_jobs (company_id);
CREATE INDEX ix_image_generation_jobs_id ON image_generation_jobs (id);
CREATE INDEX ix_image_generation_jobs_listing_package_id ON image_generation_jobs (listing_package_id);
CREATE INDEX ix_image_generation_jobs_product_candidate_id ON image_generation_jobs (product_candidate_id);
CREATE INDEX ix_image_generation_jobs_status ON image_generation_jobs (status);

CREATE TABLE image_generation_results (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	job_id INTEGER NOT NULL,
	media_asset_id INTEGER,
	sequence_index INTEGER NOT NULL,
	purpose VARCHAR(30) NOT NULL,
	status VARCHAR(20) NOT NULL,
	safety_check_status VARCHAR(20) NOT NULL,
	error_reason VARCHAR(500),
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_image_generation_results_company_id ON image_generation_results (company_id);
CREATE INDEX ix_image_generation_results_id ON image_generation_results (id);
CREATE INDEX ix_image_generation_results_job_id ON image_generation_results (job_id);
CREATE INDEX ix_image_generation_results_media_asset_id ON image_generation_results (media_asset_id);

CREATE TABLE image_generation_daily_usage (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	usage_date VARCHAR(10) NOT NULL,
	consumed_image_count INTEGER NOT NULL,
	consumed_cost NUMERIC(12, 4) NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_image_generation_daily_usage_company_date UNIQUE (company_id, usage_date)
);

CREATE INDEX ix_image_generation_daily_usage_company_id ON image_generation_daily_usage (company_id);
CREATE INDEX ix_image_generation_daily_usage_id ON image_generation_daily_usage (id);

CREATE TABLE listing_packages (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	product_candidate_id INTEGER NOT NULL,
	mode VARCHAR(20) NOT NULL,
	status VARCHAR(20) NOT NULL,
	draft_payload_json VARCHAR(8000) NOT NULL,
	channel_selection_json VARCHAR(4000) NOT NULL,
	image_options_json VARCHAR(2000) NOT NULL,
	missing_fields_json VARCHAR(4000) NOT NULL,
	estimated_revenue NUMERIC(14, 2),
	risk_summary VARCHAR(1000),
	recommendation_reason VARCHAR(2000),
	package_fingerprint VARCHAR(64) NOT NULL,
	request_fingerprint VARCHAR(64) NOT NULL,
	idempotency_key VARCHAR(160) NOT NULL,
	created_by INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_listing_packages_company_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX ix_listing_packages_company_id ON listing_packages (company_id);
CREATE INDEX ix_listing_packages_id ON listing_packages (id);
CREATE INDEX ix_listing_packages_mode ON listing_packages (mode);
CREATE INDEX ix_listing_packages_product_candidate_id ON listing_packages (product_candidate_id);
CREATE INDEX ix_listing_packages_status ON listing_packages (status);

CREATE TABLE listing_package_approvals (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	listing_package_id INTEGER NOT NULL,
	status VARCHAR(20) NOT NULL,
	package_fingerprint_snapshot VARCHAR(64) NOT NULL,
	payload_snapshot_json VARCHAR(8000) NOT NULL,
	approved_by INTEGER,
	approved_at DATETIME,
	requested_by INTEGER NOT NULL,
	requested_at DATETIME NOT NULL,
	reason VARCHAR(1000),
	idempotency_key VARCHAR(160) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_listing_package_approvals_company_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX ix_listing_package_approvals_company_id ON listing_package_approvals (company_id);
CREATE INDEX ix_listing_package_approvals_id ON listing_package_approvals (id);
CREATE INDEX ix_listing_package_approvals_listing_package_id ON listing_package_approvals (listing_package_id);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로 실행):
-- BEGIN;
-- DROP INDEX ix_listing_package_approvals_company_id;
-- DROP INDEX ix_listing_package_approvals_id;
-- DROP INDEX ix_listing_package_approvals_listing_package_id;
-- DROP TABLE listing_package_approvals;
-- DROP INDEX ix_listing_packages_company_id;
-- DROP INDEX ix_listing_packages_id;
-- DROP INDEX ix_listing_packages_mode;
-- DROP INDEX ix_listing_packages_product_candidate_id;
-- DROP INDEX ix_listing_packages_status;
-- DROP TABLE listing_packages;
-- DROP INDEX ix_image_generation_daily_usage_company_id;
-- DROP INDEX ix_image_generation_daily_usage_id;
-- DROP TABLE image_generation_daily_usage;
-- DROP INDEX ix_image_generation_results_company_id;
-- DROP INDEX ix_image_generation_results_id;
-- DROP INDEX ix_image_generation_results_job_id;
-- DROP INDEX ix_image_generation_results_media_asset_id;
-- DROP TABLE image_generation_results;
-- DROP INDEX ix_image_generation_jobs_company_id;
-- DROP INDEX ix_image_generation_jobs_id;
-- DROP INDEX ix_image_generation_jobs_listing_package_id;
-- DROP INDEX ix_image_generation_jobs_product_candidate_id;
-- DROP INDEX ix_image_generation_jobs_status;
-- DROP TABLE image_generation_jobs;
-- DROP INDEX ix_media_assets_asset_role;
-- DROP INDEX ix_media_assets_company_id;
-- DROP INDEX ix_media_assets_id;
-- DROP INDEX ix_media_assets_owner_id;
-- DROP INDEX ix_media_assets_owner_type;
-- DROP INDEX ix_media_assets_purpose;
-- DROP INDEX ix_media_assets_sha256_hex;
-- DROP INDEX ix_media_assets_source_asset_id;
-- DROP INDEX ix_media_assets_status;
-- DROP TABLE media_assets;
-- COMMIT;
