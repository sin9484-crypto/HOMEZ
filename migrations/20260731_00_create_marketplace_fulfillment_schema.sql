-- Purpose: HOMEZ 채널별 판매 방식(Fulfillment Mode) 선택 — 전체 스키마 생성.
-- Source of Truth: app/domains/marketplace_listing/model.py
--
-- 대상 9개 테이블 (Model 기준, ForeignKey 없음 — 전부 논리 참조 컬럼):
--   marketplace_channels
--   marketplace_accounts
--   marketplace_fulfillment_capabilities
--   marketplace_listing_drafts
--   marketplace_listings
--   marketplace_fulfillment_selections
--   marketplace_submission_approvals
--   marketplace_fulfillment_eligibilities
--   marketplace_submissions
--
-- 2026-07-31 독립 재감사 반영 — 이 파일은 실제 homez.db에 한 번도
-- 적용되지 않은 상태에서 그 자리에서 수정되었다(별도 후속 Migration
-- 아님):
--   - marketplace_fulfillment_capabilities에 schema_name/schema_version
--     추가(방식별 필수 입력 Pydantic Schema 식별자).
--   - marketplace_fulfillment_selections에
--     required_fields_schema_name/version/fingerprint 추가(자유 JSON
--     대신 검증된 Schema 기반 저장).
--   - marketplace_submission_approvals 테이블 신규 추가 — 제출 승인은
--     클라이언트가 자칭하는 Boolean이 아니라 서버에 저장된 별도
--     레코드다(요청/승인/거절/취소가 각각 별도 API 호출).
--
-- 2026-08-01 CTO 3차 지적 반영(같은 날, 여전히 미적용 재확인 후 그
-- 자리에서 수정) — 회사(테넌트) 소유권 경계 신규 발견·추가:
--   - marketplace_accounts/marketplace_listing_drafts/
--     marketplace_listings/marketplace_fulfillment_selections/
--     marketplace_submission_approvals/
--     marketplace_fulfillment_eligibilities/marketplace_submissions
--     전부에 company_id NOT NULL 컬럼을 추가했다(marketplace_channels/
--     marketplace_fulfillment_capabilities는 전역 플랫폼 설정이라
--     제외).
--   - idempotency_key UNIQUE 전부를 (company_id, idempotency_key)
--     복합으로 변경 — 전역 UNIQUE는 서로 다른 회사가 우연히 같은 key를
--     쓰면 IntegrityError 복구 경로가 타사 행을 반환하는 유출 경로였다
--     (StoreConnection 재감사에서 발견된 것과 동일한 근본 원인).
--   - marketplace_accounts의 UNIQUE도 (channel_id, account_code)에서
--     (company_id, channel_id, account_code)로 변경.
--
-- 판매 방식은 Product 전역 속성이 아니라 (ProductCandidate ×
-- MarketplaceAccount) 단위의 Listing 속성이다. Marketplace 공식 API
-- payload에 포함되지 않는 Safety 판단 전용 금액 필드(unit_price 등)는
-- marketplace_submission_approvals에만 존재한다. 이 Migration은 실제
-- homez.db에 적용하지 않는다. 임시 DB에서만 검증한다.
--
-- 실행 전 필수 확인: 아래 9개 테이블이 대상 DB에 하나도 존재하지 않아야
-- 한다. 일부만 존재하는 상태(부분 적용)를 이 스크립트는 감지하지 않고
-- 그대로 CREATE TABLE을 시도해 실패하도록 둔다(IF NOT EXISTS를 쓰지 않음).

BEGIN;

CREATE TABLE marketplace_channels (
	id INTEGER NOT NULL,
	code VARCHAR(30) NOT NULL,
	name VARCHAR(100) NOT NULL,
	doc_verification_status VARCHAR(20) NOT NULL,
	doc_source_reference VARCHAR(500),
	is_active BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_marketplace_channels_code UNIQUE (code)
);

CREATE INDEX ix_marketplace_channels_id ON marketplace_channels (id);
CREATE INDEX ix_marketplace_channels_is_active ON marketplace_channels (is_active);

CREATE TABLE marketplace_accounts (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	channel_id INTEGER NOT NULL,
	account_code VARCHAR(100) NOT NULL,
	account_name VARCHAR(200) NOT NULL,
	direct_purchase_contract_status VARCHAR(20) NOT NULL,
	direct_purchase_contract_reference VARCHAR(500),
	direct_purchase_contract_verified_at DATETIME,
	direct_purchase_contract_expires_at DATETIME,
	is_active BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_marketplace_accounts_company_channel_account UNIQUE (company_id, channel_id, account_code)
);

CREATE INDEX ix_marketplace_accounts_channel_id ON marketplace_accounts (channel_id);
CREATE INDEX ix_marketplace_accounts_company_id ON marketplace_accounts (company_id);
CREATE INDEX ix_marketplace_accounts_id ON marketplace_accounts (id);
CREATE INDEX ix_marketplace_accounts_is_active ON marketplace_accounts (is_active);

CREATE TABLE marketplace_fulfillment_capabilities (
	id INTEGER NOT NULL,
	channel_id INTEGER NOT NULL,
	fulfillment_mode VARCHAR(40) NOT NULL,
	is_supported BOOLEAN NOT NULL,
	requires_eligibility_check BOOLEAN NOT NULL,
	requires_account_contract BOOLEAN NOT NULL,
	external_display_name VARCHAR(100) NOT NULL,
	policy_version VARCHAR(50) NOT NULL,
	schema_name VARCHAR(80),
	schema_version VARCHAR(20),
	doc_source_reference VARCHAR(500) NOT NULL,
	status VARCHAR(20) NOT NULL,
	verified_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_marketplace_capability_channel_mode UNIQUE (channel_id, fulfillment_mode)
);

CREATE INDEX ix_marketplace_fulfillment_capabilities_channel_id ON marketplace_fulfillment_capabilities (channel_id);
CREATE INDEX ix_marketplace_fulfillment_capabilities_id ON marketplace_fulfillment_capabilities (id);
CREATE INDEX ix_marketplace_fulfillment_capabilities_status ON marketplace_fulfillment_capabilities (status);

CREATE TABLE marketplace_listing_drafts (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	product_candidate_id INTEGER NOT NULL,
	workflow_state VARCHAR(30) NOT NULL,
	selected_channel_ids_json TEXT NOT NULL,
	product_basics_json TEXT,
	validation_errors_json TEXT,
	created_by INTEGER NOT NULL,
	idempotency_key VARCHAR(160) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_marketplace_listing_drafts_company_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX ix_marketplace_listing_drafts_company_id ON marketplace_listing_drafts (company_id);
CREATE INDEX ix_marketplace_listing_drafts_id ON marketplace_listing_drafts (id);
CREATE INDEX ix_marketplace_listing_drafts_product_candidate_id ON marketplace_listing_drafts (product_candidate_id);
CREATE INDEX ix_marketplace_listing_drafts_workflow_state ON marketplace_listing_drafts (workflow_state);

CREATE TABLE marketplace_listings (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	draft_id INTEGER,
	product_candidate_id INTEGER NOT NULL,
	marketplace_account_id INTEGER NOT NULL,
	external_listing_id VARCHAR(100),
	status VARCHAR(30) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_marketplace_listings_candidate_account UNIQUE (product_candidate_id, marketplace_account_id)
);

CREATE INDEX ix_marketplace_listings_company_id ON marketplace_listings (company_id);
CREATE INDEX ix_marketplace_listings_draft_id ON marketplace_listings (draft_id);
CREATE INDEX ix_marketplace_listings_id ON marketplace_listings (id);
CREATE INDEX ix_marketplace_listings_marketplace_account_id ON marketplace_listings (marketplace_account_id);
CREATE INDEX ix_marketplace_listings_product_candidate_id ON marketplace_listings (product_candidate_id);
CREATE INDEX ix_marketplace_listings_status ON marketplace_listings (status);

CREATE TABLE marketplace_fulfillment_selections (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	listing_id INTEGER NOT NULL,
	capability_id INTEGER NOT NULL,
	fulfillment_mode VARCHAR(40) NOT NULL,
	required_fields_json TEXT NOT NULL,
	required_fields_schema_name VARCHAR(80) NOT NULL,
	required_fields_schema_version VARCHAR(20) NOT NULL,
	required_fields_fingerprint VARCHAR(64) NOT NULL,
	status VARCHAR(20) NOT NULL,
	decision_evaluation_id INTEGER,
	selected_by INTEGER NOT NULL,
	idempotency_key VARCHAR(160) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_marketplace_fulfillment_selections_company_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX ix_marketplace_fulfillment_selections_capability_id ON marketplace_fulfillment_selections (capability_id);
CREATE INDEX ix_marketplace_fulfillment_selections_company_id ON marketplace_fulfillment_selections (company_id);
CREATE INDEX ix_marketplace_fulfillment_selections_decision_evaluation_id ON marketplace_fulfillment_selections (decision_evaluation_id);
CREATE INDEX ix_marketplace_fulfillment_selections_id ON marketplace_fulfillment_selections (id);
CREATE INDEX ix_marketplace_fulfillment_selections_listing_id ON marketplace_fulfillment_selections (listing_id);
CREATE INDEX ix_marketplace_fulfillment_selections_status ON marketplace_fulfillment_selections (status);

CREATE TABLE marketplace_submission_approvals (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	listing_id INTEGER NOT NULL,
	selection_id INTEGER NOT NULL,
	status VARCHAR(20) NOT NULL,
	listing_fingerprint VARCHAR(64) NOT NULL,
	selection_fingerprint VARCHAR(64) NOT NULL,
	capability_policy_version VARCHAR(50) NOT NULL,
	planned_quantity INTEGER NOT NULL,
	unit_price NUMERIC(14, 2) NOT NULL,
	unit_cost_of_goods NUMERIC(14, 2) NOT NULL,
	expected_logistics_cost NUMERIC(14, 2) NOT NULL,
	requested_by INTEGER NOT NULL,
	requested_at DATETIME NOT NULL,
	approved_by INTEGER,
	approved_at DATETIME,
	expires_at DATETIME,
	reason VARCHAR(1000),
	idempotency_key VARCHAR(160) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_marketplace_submission_approvals_company_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX ix_marketplace_submission_approvals_company_id ON marketplace_submission_approvals (company_id);
CREATE INDEX ix_marketplace_submission_approvals_id ON marketplace_submission_approvals (id);
CREATE INDEX ix_marketplace_submission_approvals_listing_id ON marketplace_submission_approvals (listing_id);
CREATE INDEX ix_marketplace_submission_approvals_selection_id ON marketplace_submission_approvals (selection_id);
CREATE INDEX ix_marketplace_submission_approvals_status ON marketplace_submission_approvals (status);

CREATE TABLE marketplace_fulfillment_eligibilities (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	marketplace_account_id INTEGER NOT NULL,
	fulfillment_mode VARCHAR(40) NOT NULL,
	capability_id INTEGER NOT NULL,
	state VARCHAR(30) NOT NULL,
	check_source VARCHAR(50) NOT NULL,
	evidence_reference VARCHAR(500),
	checked_at DATETIME,
	verified_at DATETIME,
	expires_at DATETIME,
	reviewer_id INTEGER,
	review_reason VARCHAR(1000),
	idempotency_key VARCHAR(160) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_marketplace_fulfillment_eligibility_company_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX ix_marketplace_fulfillment_eligibilities_capability_id ON marketplace_fulfillment_eligibilities (capability_id);
CREATE INDEX ix_marketplace_fulfillment_eligibilities_company_id ON marketplace_fulfillment_eligibilities (company_id);
CREATE INDEX ix_marketplace_fulfillment_eligibilities_fulfillment_mode ON marketplace_fulfillment_eligibilities (fulfillment_mode);
CREATE INDEX ix_marketplace_fulfillment_eligibilities_id ON marketplace_fulfillment_eligibilities (id);
CREATE INDEX ix_marketplace_fulfillment_eligibilities_marketplace_account_id ON marketplace_fulfillment_eligibilities (marketplace_account_id);
CREATE INDEX ix_marketplace_fulfillment_eligibilities_state ON marketplace_fulfillment_eligibilities (state);

CREATE TABLE marketplace_submissions (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	listing_id INTEGER NOT NULL,
	selection_id INTEGER NOT NULL,
	marketplace_account_id INTEGER NOT NULL,
	external_submission_ref VARCHAR(200),
	status VARCHAR(30) NOT NULL,
	safety_decision VARCHAR(20) NOT NULL,
	operator_approved_by INTEGER,
	operator_approved_at DATETIME,
	error_reason VARCHAR(1000),
	idempotency_key VARCHAR(160) NOT NULL,
	attempted_at DATETIME NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_marketplace_submissions_company_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX ix_marketplace_submissions_company_id ON marketplace_submissions (company_id);
CREATE INDEX ix_marketplace_submissions_id ON marketplace_submissions (id);
CREATE INDEX ix_marketplace_submissions_listing_id ON marketplace_submissions (listing_id);
CREATE INDEX ix_marketplace_submissions_marketplace_account_id ON marketplace_submissions (marketplace_account_id);
CREATE INDEX ix_marketplace_submissions_selection_id ON marketplace_submissions (selection_id);
CREATE INDEX ix_marketplace_submissions_status ON marketplace_submissions (status);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로 실행):
-- BEGIN;
-- DROP INDEX ix_marketplace_submissions_company_id;
-- DROP INDEX ix_marketplace_submissions_id;
-- DROP INDEX ix_marketplace_submissions_listing_id;
-- DROP INDEX ix_marketplace_submissions_marketplace_account_id;
-- DROP INDEX ix_marketplace_submissions_selection_id;
-- DROP INDEX ix_marketplace_submissions_status;
-- DROP TABLE marketplace_submissions;
-- DROP INDEX ix_marketplace_fulfillment_eligibilities_capability_id;
-- DROP INDEX ix_marketplace_fulfillment_eligibilities_company_id;
-- DROP INDEX ix_marketplace_fulfillment_eligibilities_fulfillment_mode;
-- DROP INDEX ix_marketplace_fulfillment_eligibilities_id;
-- DROP INDEX ix_marketplace_fulfillment_eligibilities_marketplace_account_id;
-- DROP INDEX ix_marketplace_fulfillment_eligibilities_state;
-- DROP TABLE marketplace_fulfillment_eligibilities;
-- DROP INDEX ix_marketplace_submission_approvals_company_id;
-- DROP INDEX ix_marketplace_submission_approvals_id;
-- DROP INDEX ix_marketplace_submission_approvals_listing_id;
-- DROP INDEX ix_marketplace_submission_approvals_selection_id;
-- DROP INDEX ix_marketplace_submission_approvals_status;
-- DROP TABLE marketplace_submission_approvals;
-- DROP INDEX ix_marketplace_fulfillment_selections_capability_id;
-- DROP INDEX ix_marketplace_fulfillment_selections_company_id;
-- DROP INDEX ix_marketplace_fulfillment_selections_decision_evaluation_id;
-- DROP INDEX ix_marketplace_fulfillment_selections_id;
-- DROP INDEX ix_marketplace_fulfillment_selections_listing_id;
-- DROP INDEX ix_marketplace_fulfillment_selections_status;
-- DROP TABLE marketplace_fulfillment_selections;
-- DROP INDEX ix_marketplace_listings_company_id;
-- DROP INDEX ix_marketplace_listings_draft_id;
-- DROP INDEX ix_marketplace_listings_id;
-- DROP INDEX ix_marketplace_listings_marketplace_account_id;
-- DROP INDEX ix_marketplace_listings_product_candidate_id;
-- DROP INDEX ix_marketplace_listings_status;
-- DROP TABLE marketplace_listings;
-- DROP INDEX ix_marketplace_listing_drafts_company_id;
-- DROP INDEX ix_marketplace_listing_drafts_id;
-- DROP INDEX ix_marketplace_listing_drafts_product_candidate_id;
-- DROP INDEX ix_marketplace_listing_drafts_workflow_state;
-- DROP TABLE marketplace_listing_drafts;
-- DROP INDEX ix_marketplace_fulfillment_capabilities_channel_id;
-- DROP INDEX ix_marketplace_fulfillment_capabilities_id;
-- DROP INDEX ix_marketplace_fulfillment_capabilities_status;
-- DROP TABLE marketplace_fulfillment_capabilities;
-- DROP INDEX ix_marketplace_accounts_channel_id;
-- DROP INDEX ix_marketplace_accounts_company_id;
-- DROP INDEX ix_marketplace_accounts_id;
-- DROP INDEX ix_marketplace_accounts_is_active;
-- DROP TABLE marketplace_accounts;
-- DROP INDEX ix_marketplace_channels_id;
-- DROP INDEX ix_marketplace_channels_is_active;
-- DROP TABLE marketplace_channels;
-- COMMIT;
