-- Purpose: Gate RP-1(2026-08-22 CTO 지시, 2026-08-22 14차 지시로
-- 확장) — 대형 쇼핑몰(네이버·11번가·G마켓·옥션 등) 구매 실행 중립
-- 인프라 4개 테이블.
-- Source of Truth: app/domains/retail_purchase/model.py
--
-- 소비자 쇼핑몰 계정 아이디·비밀번호·카드정보·결제 PIN은 이 스키마
-- 어디에도 없다(이번 라운드 범위 축소 — 소비자 계정 자동 로그인·
-- 화면 자동화 결제는 구현하지 않는다는 사용자 명시 결정). 실제
-- 구매 실행(place_order)은 공식 API 또는 서면 계약된 구매대행
-- Provider로만 연결되며, 실 계약 전에는 항상 차단된다
-- (app/domains/retail_purchase/provider.py::LiveInputRequiredError).
--
-- 14차 지시로 추가된 컬럼/테이블:
--   retail_purchase_orders.policy_fingerprint,
--   retail_purchase_orders.uncertain_budget_released
--   retail_purchase_policy_settings.max_quantity_per_product,
--   retail_purchase_policy_settings.auto_execute_enabled,
--   retail_purchase_policy_settings.approval_required_amount_threshold,
--   retail_purchase_policy_settings.uncertain_handling_policy,
--   retail_purchase_policy_settings.uncertain_auto_release_after_hours,
--   retail_purchase_webhook_events(신규 테이블 — Webhook 재사용 방지)
--
-- 이 Migration은 아직 실제 homez.db(개발·운영 모두)에 적용된 적이
-- 없다(2026-08-22 확인 — schema_migrations에 없음). 그래서 이미
-- 적용된 파일처럼 별도 증분 파일을 만들지 않고 이 파일 자체를
-- 그대로 갱신했다(homez-migration-safety 원칙: 미적용 파일은 그
-- 자리에서 수정, 적용된 파일만 증분). 임시 SQLite 파일에서만
-- 검증했다(tests/test_retail_purchase_service.py,
-- tests/test_retail_purchase_matching.py,
-- tests/test_retail_purchase_router.py,
-- tests/test_retail_purchase_webhook.py,
-- tests/test_retail_purchase_migration.py).

BEGIN;

CREATE TABLE retail_purchase_orders (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	source_order_id INTEGER NOT NULL,
	provider_code VARCHAR(30) NOT NULL,
	payment_account_reference_id INTEGER,
	product_url VARCHAR(1000) NOT NULL,
	external_product_id VARCHAR(200) NOT NULL,
	selected_option VARCHAR(500),
	quantity INTEGER NOT NULL,
	match_confidence FLOAT,
	match_evidence_json VARCHAR(4000) NOT NULL,
	expected_amount FLOAT,
	actual_amount FLOAT,
	expected_net_profit FLOAT,
	external_order_id VARCHAR(200),
	external_order_number VARCHAR(200),
	status VARCHAR(30) NOT NULL,
	tracking_company VARCHAR(100),
	tracking_number VARCHAR(200),
	idempotency_key VARCHAR(150) NOT NULL,
	correlation_id VARCHAR(100),
	funding_hold_id INTEGER,
	failure_code VARCHAR(100),
	retryable BOOLEAN NOT NULL,
	retry_after DATETIME,
	policy_fingerprint VARCHAR(64),
	uncertain_budget_released BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_retail_purchase_orders_company_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX ix_retail_purchase_orders_source_order_id ON retail_purchase_orders (source_order_id);
CREATE INDEX ix_retail_purchase_orders_provider_code ON retail_purchase_orders (provider_code);
CREATE INDEX ix_retail_purchase_orders_company_id ON retail_purchase_orders (company_id);
CREATE INDEX ix_retail_purchase_orders_id ON retail_purchase_orders (id);
CREATE INDEX ix_retail_purchase_orders_status ON retail_purchase_orders (status);

CREATE TABLE payment_account_references (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	provider_code VARCHAR(30) NOT NULL,
	external_account_reference VARCHAR(300),
	status VARCHAR(30) NOT NULL,
	balance_or_credit_snapshot FLOAT,
	last_synced_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_payment_account_references_company_provider UNIQUE (company_id, provider_code)
);

CREATE INDEX ix_payment_account_references_company_id ON payment_account_references (company_id);
CREATE INDEX ix_payment_account_references_provider_code ON payment_account_references (provider_code);
CREATE INDEX ix_payment_account_references_status ON payment_account_references (status);
CREATE INDEX ix_payment_account_references_id ON payment_account_references (id);

CREATE TABLE retail_purchase_policy_settings (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	min_net_profit FLOAT NOT NULL,
	min_margin_rate FLOAT NOT NULL,
	max_purchase_price FLOAT,
	max_price_increase_rate FLOAT NOT NULL,
	max_delivery_days INTEGER,
	require_return_allowed BOOLEAN NOT NULL,
	min_seller_trust_score FLOAT NOT NULL,
	min_match_confidence FLOAT NOT NULL,
	allowed_provider_codes_json VARCHAR(500) NOT NULL,
	per_order_max_amount FLOAT,
	daily_purchase_limit_amount FLOAT,
	monthly_purchase_budget_amount FLOAT,
	max_concurrent_orders INTEGER,
	max_quantity_per_product INTEGER,
	auto_execute_enabled BOOLEAN NOT NULL,
	approval_required_amount_threshold FLOAT,
	uncertain_handling_policy VARCHAR(30) NOT NULL,
	uncertain_auto_release_after_hours INTEGER,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_retail_purchase_policy_settings_company_id ON retail_purchase_policy_settings (company_id);
CREATE INDEX ix_retail_purchase_policy_settings_id ON retail_purchase_policy_settings (id);

CREATE TABLE retail_purchase_webhook_events (
	id INTEGER NOT NULL,
	provider_code VARCHAR(30) NOT NULL,
	event_id VARCHAR(200) NOT NULL,
	received_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_retail_purchase_webhook_events_provider_event UNIQUE (provider_code, event_id)
);

CREATE INDEX ix_retail_purchase_webhook_events_id ON retail_purchase_webhook_events (id);
CREATE INDEX ix_retail_purchase_webhook_events_provider_code ON retail_purchase_webhook_events (provider_code);

COMMIT;

-- Rollback (주석 — 자동 실행 안 됨):
-- BEGIN;
-- DROP INDEX ix_retail_purchase_webhook_events_provider_code;
-- DROP INDEX ix_retail_purchase_webhook_events_id;
-- DROP TABLE retail_purchase_webhook_events;
-- DROP INDEX ix_retail_purchase_policy_settings_id;
-- DROP INDEX ix_retail_purchase_policy_settings_company_id;
-- DROP TABLE retail_purchase_policy_settings;
-- DROP INDEX ix_payment_account_references_id;
-- DROP INDEX ix_payment_account_references_status;
-- DROP INDEX ix_payment_account_references_provider_code;
-- DROP INDEX ix_payment_account_references_company_id;
-- DROP TABLE payment_account_references;
-- DROP INDEX ix_retail_purchase_orders_status;
-- DROP INDEX ix_retail_purchase_orders_id;
-- DROP INDEX ix_retail_purchase_orders_company_id;
-- DROP INDEX ix_retail_purchase_orders_provider_code;
-- DROP INDEX ix_retail_purchase_orders_source_order_id;
-- DROP TABLE retail_purchase_orders;
-- COMMIT;
