-- Purpose: Gate PT-1(2026-08-22 15차 지시) — A(구매 링크 전달)+
-- E(구매 작업 큐)+F(주문번호·송장 가져오기) 혼합형 매입·발주
-- Workflow 신규 10개 테이블(Gate PT-2, 16차 지시에서
-- purchase_task_email_provider_settings 추가 + purchase_tasks에
-- creation_source 컬럼 추가 — 아직 미적용이라 같은 파일을 직접
-- 수정했다, 별도 후속 파일 아님).
-- Source of Truth: app/domains/purchase_task/model.py
--
-- 소비자 쇼핑몰 계정 아이디·비밀번호·카드번호·CVC·PIN·결제 승인
-- Credential은 이 스키마 어디에도 없다 — 사람이 직접 쇼핑몰에
-- 로그인·결제하고, 그 결과(주문번호·실제 결제금액)만 HOMEZ에
-- 입력한다.
--
-- 이 도메인은 `app/domains/retail_purchase/`(Provider 계약 기반,
-- Gate RP-1/RP-2)를 대체하지 않는다 — 그 도메인은 미래 확장용으로
-- 그대로 보존하고, 이 Migration은 완전히 새로운 9개 테이블만
-- 추가한다(기존 retail_purchase_* 테이블은 건드리지 않음).
--
-- 이 Migration은 아직 실제 homez.db(개발·운영 모두)에 적용된 적이
-- 없다 — 임시 SQLite 파일에서만 검증했다
-- (tests/test_purchase_task_migration.py).

BEGIN;

CREATE TABLE purchase_tasks (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	source_order_id INTEGER NOT NULL,
	source_order_item_id INTEGER,
	creation_source VARCHAR(20) NOT NULL,
	product_title VARCHAR(500) NOT NULL,
	brand VARCHAR(200),
	manufacturer VARCHAR(200),
	model_name VARCHAR(200),
	gtin VARCHAR(50),
	capacity VARCHAR(100),
	quantity INTEGER NOT NULL,
	color_or_scent VARCHAR(100),
	options_json VARCHAR(2000) NOT NULL,
	components_json VARCHAR(2000) NOT NULL,
	shippable_region_note VARCHAR(500),
	status VARCHAR(30) NOT NULL,
	version INTEGER NOT NULL,
	purchase_deadline DATETIME,
	selected_candidate_id INTEGER,
	coupang_sale_amount FLOAT,
	coupang_fee_amount FLOAT,
	expected_net_profit FLOAT,
	expected_margin_rate FLOAT,
	block_reason VARCHAR(200),
	caution_reason VARCHAR(200),
	failure_code VARCHAR(100),
	retryable BOOLEAN NOT NULL,
	budget_reservation_id INTEGER,
	policy_fingerprint VARCHAR(64),
	correlation_id VARCHAR(100),
	idempotency_key VARCHAR(150) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_purchase_tasks_company_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX ix_purchase_tasks_source_order_item_id ON purchase_tasks (source_order_item_id);
CREATE INDEX ix_purchase_tasks_id ON purchase_tasks (id);
CREATE INDEX ix_purchase_tasks_source_order_id ON purchase_tasks (source_order_id);
CREATE INDEX ix_purchase_tasks_status ON purchase_tasks (status);
CREATE INDEX ix_purchase_tasks_company_id ON purchase_tasks (company_id);

CREATE TABLE purchase_task_candidates (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	purchase_task_id INTEGER NOT NULL,
	shopping_mall_code VARCHAR(30) NOT NULL,
	product_url VARCHAR(1000) NOT NULL,
	candidate_title VARCHAR(500),
	brand VARCHAR(200),
	manufacturer VARCHAR(200),
	model_name VARCHAR(200),
	gtin VARCHAR(50),
	capacity VARCHAR(100),
	color_or_scent VARCHAR(100),
	options_json VARCHAR(2000) NOT NULL,
	estimated_price FLOAT,
	estimated_shipping_fee FLOAT,
	confirmed_additional_cost FLOAT NOT NULL,
	confirmed_discount FLOAT NOT NULL,
	estimated_delivery_days INTEGER,
	seller_trust_score FLOAT,
	return_allowed BOOLEAN,
	match_confidence FLOAT,
	match_tier VARCHAR(20),
	match_evidence_json VARCHAR(4000) NOT NULL,
	match_confirmed_by INTEGER,
	match_confirmed_at DATETIME,
	match_fingerprint VARCHAR(64),
	is_selected BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_purchase_task_candidates_task_url UNIQUE (purchase_task_id, product_url)
);

CREATE INDEX ix_purchase_task_candidates_shopping_mall_code ON purchase_task_candidates (shopping_mall_code);
CREATE INDEX ix_purchase_task_candidates_id ON purchase_task_candidates (id);
CREATE INDEX ix_purchase_task_candidates_company_id ON purchase_task_candidates (company_id);
CREATE INDEX ix_purchase_task_candidates_purchase_task_id ON purchase_task_candidates (purchase_task_id);

CREATE TABLE purchase_task_budget_reservations (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	purchase_task_id INTEGER NOT NULL,
	account_id INTEGER NOT NULL,
	amount FLOAT NOT NULL,
	status VARCHAR(20) NOT NULL,
	reserved_at DATETIME NOT NULL,
	expires_at DATETIME NOT NULL,
	extended_at DATETIME,
	released_at DATETIME,
	confirmed_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_purchase_task_budget_reservations_account_id ON purchase_task_budget_reservations (account_id);
CREATE INDEX ix_purchase_task_budget_reservations_status ON purchase_task_budget_reservations (status);
CREATE INDEX ix_purchase_task_budget_reservations_company_id ON purchase_task_budget_reservations (company_id);
CREATE INDEX ix_purchase_task_budget_reservations_id ON purchase_task_budget_reservations (id);
CREATE INDEX ix_purchase_task_budget_reservations_purchase_task_id ON purchase_task_budget_reservations (purchase_task_id);

CREATE TABLE purchase_records (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	purchase_task_id INTEGER NOT NULL,
	shopping_mall_code VARCHAR(30) NOT NULL,
	external_order_number VARCHAR(200) NOT NULL,
	actual_amount FLOAT NOT NULL,
	actual_shipping_fee FLOAT,
	purchased_at DATETIME NOT NULL,
	selected_option_note VARCHAR(500),
	memo VARCHAR(1000),
	recorded_by INTEGER NOT NULL,
	idempotency_key VARCHAR(150) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_purchase_records_company_mall_order_number UNIQUE (company_id, shopping_mall_code, external_order_number),
	CONSTRAINT uq_purchase_records_company_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX ix_purchase_records_company_id ON purchase_records (company_id);
CREATE INDEX ix_purchase_records_id ON purchase_records (id);
CREATE INDEX ix_purchase_records_purchase_task_id ON purchase_records (purchase_task_id);

CREATE TABLE purchase_task_tracking_infos (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	purchase_task_id INTEGER NOT NULL,
	courier VARCHAR(100),
	courier_confirmed BOOLEAN NOT NULL,
	tracking_number VARCHAR(200),
	shipped_at DATETIME,
	expected_arrival_at DATETIME,
	delivery_status VARCHAR(50),
	is_partial_shipment BOOLEAN NOT NULL,
	cancel_status VARCHAR(30),
	return_status VARCHAR(30),
	refund_status VARCHAR(30),
	refund_amount FLOAT,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_purchase_task_tracking_infos_task UNIQUE (purchase_task_id)
);

CREATE INDEX ix_purchase_task_tracking_infos_id ON purchase_task_tracking_infos (id);
CREATE INDEX ix_purchase_task_tracking_infos_purchase_task_id ON purchase_task_tracking_infos (purchase_task_id);
CREATE INDEX ix_purchase_task_tracking_infos_company_id ON purchase_task_tracking_infos (company_id);

CREATE TABLE purchase_task_email_preferences (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	enabled BOOLEAN NOT NULL,
	event_toggles_json VARCHAR(2000) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_purchase_task_email_preferences_company_user UNIQUE (company_id, user_id)
);

CREATE INDEX ix_purchase_task_email_preferences_company_id ON purchase_task_email_preferences (company_id);
CREATE INDEX ix_purchase_task_email_preferences_id ON purchase_task_email_preferences (id);
CREATE INDEX ix_purchase_task_email_preferences_user_id ON purchase_task_email_preferences (user_id);

CREATE TABLE purchase_task_email_logs (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	purchase_task_id INTEGER,
	user_id INTEGER NOT NULL,
	event_type VARCHAR(50) NOT NULL,
	locale VARCHAR(10) NOT NULL,
	status VARCHAR(30) NOT NULL,
	idempotency_key VARCHAR(150) NOT NULL,
	attempt_count INTEGER NOT NULL,
	error_detail VARCHAR(500),
	created_at DATETIME NOT NULL,
	sent_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT uq_purchase_task_email_logs_company_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX ix_purchase_task_email_logs_id ON purchase_task_email_logs (id);
CREATE INDEX ix_purchase_task_email_logs_status ON purchase_task_email_logs (status);
CREATE INDEX ix_purchase_task_email_logs_user_id ON purchase_task_email_logs (user_id);
CREATE INDEX ix_purchase_task_email_logs_company_id ON purchase_task_email_logs (company_id);
CREATE INDEX ix_purchase_task_email_logs_purchase_task_id ON purchase_task_email_logs (purchase_task_id);

CREATE TABLE purchase_task_policy_settings (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	per_order_max_amount FLOAT,
	daily_purchase_limit_amount FLOAT,
	monthly_purchase_budget_amount FLOAT,
	max_quantity_per_product INTEGER,
	min_net_profit FLOAT NOT NULL,
	min_margin_rate FLOAT NOT NULL,
	max_price_increase_rate FLOAT NOT NULL,
	max_delivery_days INTEGER,
	require_return_allowed BOOLEAN NOT NULL,
	min_match_confidence FLOAT NOT NULL,
	max_concurrent_tasks INTEGER,
	default_additional_shipping_fee FLOAT NOT NULL,
	default_return_risk_reserve FLOAT NOT NULL,
	budget_reservation_hours INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_purchase_task_policy_settings_company_id ON purchase_task_policy_settings (company_id);
CREATE INDEX ix_purchase_task_policy_settings_id ON purchase_task_policy_settings (id);

CREATE TABLE purchase_task_csv_import_logs (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	uploaded_by INTEGER NOT NULL,
	total_rows INTEGER NOT NULL,
	success_rows INTEGER NOT NULL,
	failure_rows INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_purchase_task_csv_import_logs_company_id ON purchase_task_csv_import_logs (company_id);
CREATE INDEX ix_purchase_task_csv_import_logs_id ON purchase_task_csv_import_logs (id);

CREATE TABLE purchase_task_email_provider_settings (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	provider_type VARCHAR(30) NOT NULL,
	smtp_host VARCHAR(200),
	smtp_port INTEGER,
	smtp_use_tls BOOLEAN NOT NULL,
	from_address VARCHAR(200),
	from_name VARCHAR(200),
	credential_reference VARCHAR(200),
	is_active BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_purchase_task_email_provider_settings_id ON purchase_task_email_provider_settings (id);
CREATE UNIQUE INDEX ix_purchase_task_email_provider_settings_company_id ON purchase_task_email_provider_settings (company_id);

COMMIT;

-- Rollback (주석 — 자동 실행 안 됨):
-- BEGIN;
-- DROP INDEX ix_purchase_task_email_provider_settings_company_id;
-- DROP INDEX ix_purchase_task_email_provider_settings_id;
-- DROP TABLE purchase_task_email_provider_settings;
-- DROP INDEX ix_purchase_task_csv_import_logs_id;
-- DROP INDEX ix_purchase_task_csv_import_logs_company_id;
-- DROP TABLE purchase_task_csv_import_logs;
-- DROP INDEX ix_purchase_task_policy_settings_id;
-- DROP INDEX ix_purchase_task_policy_settings_company_id;
-- DROP TABLE purchase_task_policy_settings;
-- DROP INDEX ix_purchase_task_email_logs_purchase_task_id;
-- DROP INDEX ix_purchase_task_email_logs_company_id;
-- DROP INDEX ix_purchase_task_email_logs_user_id;
-- DROP INDEX ix_purchase_task_email_logs_status;
-- DROP INDEX ix_purchase_task_email_logs_id;
-- DROP TABLE purchase_task_email_logs;
-- DROP INDEX ix_purchase_task_email_preferences_user_id;
-- DROP INDEX ix_purchase_task_email_preferences_id;
-- DROP INDEX ix_purchase_task_email_preferences_company_id;
-- DROP TABLE purchase_task_email_preferences;
-- DROP INDEX ix_purchase_task_tracking_infos_company_id;
-- DROP INDEX ix_purchase_task_tracking_infos_purchase_task_id;
-- DROP INDEX ix_purchase_task_tracking_infos_id;
-- DROP TABLE purchase_task_tracking_infos;
-- DROP INDEX ix_purchase_records_purchase_task_id;
-- DROP INDEX ix_purchase_records_id;
-- DROP INDEX ix_purchase_records_company_id;
-- DROP TABLE purchase_records;
-- DROP INDEX ix_purchase_task_budget_reservations_purchase_task_id;
-- DROP INDEX ix_purchase_task_budget_reservations_id;
-- DROP INDEX ix_purchase_task_budget_reservations_company_id;
-- DROP INDEX ix_purchase_task_budget_reservations_status;
-- DROP INDEX ix_purchase_task_budget_reservations_account_id;
-- DROP TABLE purchase_task_budget_reservations;
-- DROP INDEX ix_purchase_task_candidates_purchase_task_id;
-- DROP INDEX ix_purchase_task_candidates_company_id;
-- DROP INDEX ix_purchase_task_candidates_id;
-- DROP INDEX ix_purchase_task_candidates_shopping_mall_code;
-- DROP TABLE purchase_task_candidates;
-- DROP INDEX ix_purchase_tasks_company_id;
-- DROP INDEX ix_purchase_tasks_status;
-- DROP INDEX ix_purchase_tasks_source_order_id;
-- DROP INDEX ix_purchase_tasks_id;
-- DROP INDEX ix_purchase_tasks_source_order_item_id;
-- DROP TABLE purchase_tasks;
-- COMMIT;
