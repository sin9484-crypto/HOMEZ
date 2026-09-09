-- Purpose: HOMEZ V3.1 Coupang Marketplace Integration Foundation — 전체 스키마 생성.
-- Source of Truth: app/domains/coupang/model.py
--
-- 대상 8개 테이블 (Model 기준, ForeignKey 없음 — 전부 논리 참조 컬럼):
--   coupang_marketplace_products
--   coupang_product_options
--   coupang_policy_sets
--   coupang_policy_rules
--   coupang_product_notices
--   coupang_profit_estimates
--   coupang_dry_run_attempts
--   coupang_integration_decisions
--
-- 개정 이력: 2026-07-29 최초 작성(6개 테이블) 이후, 같은 날 재감사
-- (Cursor/ChatGPT) 결과 반영으로 coupang_policy_rules 구조를
-- match_field/match_value 기반으로 변경하고 coupang_policy_sets(정책
-- 세트 헤더, VERIFIED 상태·완전성·유효기간으로 "사용 가능" 여부를
-- 판단)와 coupang_product_notices(고시정보)를 추가해 6개→8개 테이블로
-- 재작성했다. **이 파일은 최초 작성 이후 단 한 번도 실제 homez.db에
-- 적용된 적이 없음을 재확인**(크기 303104 bytes·mtime 2026-07-27
-- 22:38:38 불변)했으므로, 별도의 후속 Migration을 만드는 대신 같은
-- 파일을 최신 Model 기준 canonical DDL로 완전히 재작성하는 방식을
-- 선택했다(이미 적용된 Migration이었다면 반드시 후속 파일을 새로
-- 만들어야 했을 것이다).
--
-- 이번 단계(Foundation)는 실제 쿠팡 API 호출·실제 상품 등록·실제 주문
-- 수집을 하지 않는다. 이 Migration도 실제 homez.db에 적용하지 않는다.
-- 임시 DB에서만 검증한다.
--
-- 실행 전 필수 확인: 아래 8개 테이블이 대상 DB에 하나도 존재하지 않아야
-- 한다. 일부만 존재하는 상태(부분 적용)를 이 스크립트는 감지하지 않고
-- 그대로 CREATE TABLE을 시도해 실패하도록 둔다(IF NOT EXISTS를 쓰지 않음).

BEGIN;

CREATE TABLE coupang_marketplace_products (
	id INTEGER NOT NULL,
	marketplace VARCHAR(30) NOT NULL,
	sales_method VARCHAR(30) NOT NULL,
	product_candidate_id INTEGER NOT NULL,
	seller_product_id VARCHAR(100),
	vendor_item_id VARCHAR(100),
	external_vendor_sku VARCHAR(100) NOT NULL,
	display_category_code VARCHAR(50),
	seller_product_name VARCHAR(300) NOT NULL,
	brand VARCHAR(100),
	gtin VARCHAR(50),
	mpn VARCHAR(50),
	identifier_exemption_reason VARCHAR(500),
	sale_price NUMERIC(14, 2),
	supplier_stock INTEGER,
	safety_stock INTEGER NOT NULL,
	marketplace_exposure_stock INTEGER NOT NULL,
	last_stock_checked_at DATETIME,
	stock_review_required BOOLEAN NOT NULL,
	available_stock INTEGER NOT NULL,
	maximum_buy_count INTEGER,
	shipping_method VARCHAR(50),
	shipping_company_code VARCHAR(50),
	outbound_shipping_place_code VARCHAR(50),
	return_center_code VARCHAR(50),
	return_charge NUMERIC(14, 2),
	overseas_purchase_agency BOOLEAN NOT NULL,
	pcc_needed BOOLEAN NOT NULL,
	status VARCHAR(30) NOT NULL,
	validation_status VARCHAR(20) NOT NULL,
	validation_errors TEXT,
	risk_level VARCHAR(30),
	policy_version_applied VARCHAR(50),
	idempotency_key VARCHAR(120) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_coupang_products_idempotency UNIQUE (idempotency_key)
);

CREATE INDEX ix_coupang_marketplace_products_external_vendor_sku ON coupang_marketplace_products (external_vendor_sku);
CREATE INDEX ix_coupang_marketplace_products_id ON coupang_marketplace_products (id);
CREATE INDEX ix_coupang_marketplace_products_product_candidate_id ON coupang_marketplace_products (product_candidate_id);
CREATE INDEX ix_coupang_marketplace_products_status ON coupang_marketplace_products (status);

CREATE TABLE coupang_product_options (
	id INTEGER NOT NULL,
	coupang_product_id INTEGER NOT NULL,
	option_name VARCHAR(100) NOT NULL,
	option_value VARCHAR(100) NOT NULL,
	vendor_sku VARCHAR(100) NOT NULL,
	price NUMERIC(14, 2) NOT NULL,
	stock INTEGER NOT NULL,
	barcode VARCHAR(50),
	status VARCHAR(20) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_coupang_product_options_coupang_product_id ON coupang_product_options (coupang_product_id);
CREATE INDEX ix_coupang_product_options_id ON coupang_product_options (id);

CREATE TABLE coupang_policy_sets (
	id INTEGER NOT NULL,
	policy_set_id VARCHAR(100) NOT NULL,
	policy_version VARCHAR(50) NOT NULL,
	source_reference VARCHAR(500) NOT NULL,
	status VARCHAR(20) NOT NULL,
	is_complete BOOLEAN NOT NULL,
	is_active BOOLEAN NOT NULL,
	checked_at DATETIME NOT NULL,
	effective_at DATETIME NOT NULL,
	expires_at DATETIME,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_coupang_policy_sets_policy_set_id UNIQUE (policy_set_id)
);

CREATE INDEX ix_coupang_policy_sets_id ON coupang_policy_sets (id);
CREATE INDEX ix_coupang_policy_sets_is_active ON coupang_policy_sets (is_active);
CREATE INDEX ix_coupang_policy_sets_status ON coupang_policy_sets (status);

CREATE TABLE coupang_policy_rules (
	id INTEGER NOT NULL,
	policy_set_id INTEGER NOT NULL,
	match_field VARCHAR(50) NOT NULL,
	match_value VARCHAR(200) NOT NULL,
	risk_level VARCHAR(30) NOT NULL,
	reason VARCHAR(500) NOT NULL,
	is_active BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_coupang_policy_rules_id ON coupang_policy_rules (id);
CREATE INDEX ix_coupang_policy_rules_is_active ON coupang_policy_rules (is_active);
CREATE INDEX ix_coupang_policy_rules_policy_set_id ON coupang_policy_rules (policy_set_id);

CREATE TABLE coupang_product_notices (
	id INTEGER NOT NULL,
	coupang_product_id INTEGER NOT NULL,
	notice_category_name VARCHAR(100) NOT NULL,
	notice_category_detail_name VARCHAR(200) NOT NULL,
	content VARCHAR(2000) NOT NULL,
	source VARCHAR(500) NOT NULL,
	verified_at DATETIME,
	status VARCHAR(20) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_coupang_product_notices_coupang_product_id ON coupang_product_notices (coupang_product_id);
CREATE INDEX ix_coupang_product_notices_id ON coupang_product_notices (id);

CREATE TABLE coupang_profit_estimates (
	id INTEGER NOT NULL,
	coupang_product_id INTEGER NOT NULL,
	consumer_sale_price NUMERIC(14, 2) NOT NULL,
	supplier_product_cost NUMERIC(14, 2) NOT NULL,
	supplier_shipping_cost NUMERIC(14, 2) NOT NULL,
	coupang_sales_fee NUMERIC(14, 2),
	coupang_shipping_cost NUMERIC(14, 2) NOT NULL,
	advertising_cost NUMERIC(14, 2) NOT NULL,
	coupon_cost NUMERIC(14, 2) NOT NULL,
	expected_return_cost NUMERIC(14, 2) NOT NULL,
	rocket_growth_cost NUMERIC(14, 2) NOT NULL,
	other_deductions NUMERIC(14, 2) NOT NULL,
	vat_or_tax_estimate NUMERIC(14, 2) NOT NULL,
	gross_revenue NUMERIC(14, 2) NOT NULL,
	marketplace_fee_total NUMERIC(14, 2) NOT NULL,
	supplier_payment_estimate NUMERIC(14, 2) NOT NULL,
	expected_net_settlement NUMERIC(14, 2) NOT NULL,
	expected_profit NUMERIC(14, 2) NOT NULL,
	expected_margin_rate NUMERIC(9, 4) NOT NULL,
	required_funding NUMERIC(14, 2) NOT NULL,
	break_even_price NUMERIC(14, 2) NOT NULL,
	meets_minimum_margin BOOLEAN NOT NULL,
	calculation_version VARCHAR(20) NOT NULL,
	calculated_at DATETIME NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_coupang_profit_estimates_coupang_product_id ON coupang_profit_estimates (coupang_product_id);
CREATE INDEX ix_coupang_profit_estimates_id ON coupang_profit_estimates (id);

CREATE TABLE coupang_dry_run_attempts (
	id INTEGER NOT NULL,
	coupang_product_id INTEGER NOT NULL,
	idempotency_key VARCHAR(120) NOT NULL,
	outcome VARCHAR(20) NOT NULL,
	errors TEXT,
	payload_field_count INTEGER NOT NULL,
	attempted_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_coupang_dry_run_idempotency UNIQUE (idempotency_key)
);

CREATE INDEX ix_coupang_dry_run_attempts_coupang_product_id ON coupang_dry_run_attempts (coupang_product_id);
CREATE INDEX ix_coupang_dry_run_attempts_id ON coupang_dry_run_attempts (id);

CREATE TABLE coupang_integration_decisions (
	id INTEGER NOT NULL,
	coupang_product_id INTEGER NOT NULL,
	action VARCHAR(40) NOT NULL,
	operator_id INTEGER NOT NULL,
	idempotency_key VARCHAR(120) NOT NULL,
	memo VARCHAR(1000),
	decided_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_coupang_decisions_idempotency UNIQUE (idempotency_key)
);

CREATE INDEX ix_coupang_integration_decisions_coupang_product_id ON coupang_integration_decisions (coupang_product_id);
CREATE INDEX ix_coupang_integration_decisions_id ON coupang_integration_decisions (id);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로 실행):
-- BEGIN;
-- DROP INDEX ix_coupang_integration_decisions_coupang_product_id;
-- DROP INDEX ix_coupang_integration_decisions_id;
-- DROP TABLE coupang_integration_decisions;
-- DROP INDEX ix_coupang_dry_run_attempts_coupang_product_id;
-- DROP INDEX ix_coupang_dry_run_attempts_id;
-- DROP TABLE coupang_dry_run_attempts;
-- DROP INDEX ix_coupang_profit_estimates_coupang_product_id;
-- DROP INDEX ix_coupang_profit_estimates_id;
-- DROP TABLE coupang_profit_estimates;
-- DROP INDEX ix_coupang_product_notices_coupang_product_id;
-- DROP INDEX ix_coupang_product_notices_id;
-- DROP TABLE coupang_product_notices;
-- DROP INDEX ix_coupang_policy_rules_id;
-- DROP INDEX ix_coupang_policy_rules_is_active;
-- DROP INDEX ix_coupang_policy_rules_policy_set_id;
-- DROP TABLE coupang_policy_rules;
-- DROP INDEX ix_coupang_policy_sets_id;
-- DROP INDEX ix_coupang_policy_sets_is_active;
-- DROP INDEX ix_coupang_policy_sets_status;
-- DROP TABLE coupang_policy_sets;
-- DROP INDEX ix_coupang_product_options_coupang_product_id;
-- DROP INDEX ix_coupang_product_options_id;
-- DROP TABLE coupang_product_options;
-- DROP INDEX ix_coupang_marketplace_products_external_vendor_sku;
-- DROP INDEX ix_coupang_marketplace_products_id;
-- DROP INDEX ix_coupang_marketplace_products_product_candidate_id;
-- DROP INDEX ix_coupang_marketplace_products_status;
-- DROP TABLE coupang_marketplace_products;
-- COMMIT;
