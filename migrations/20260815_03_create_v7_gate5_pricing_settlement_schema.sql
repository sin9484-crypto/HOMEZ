-- Purpose: V7 Gate 5(2026-08-15) — 가격/원가/마진 계산, 가격 변경
-- 승인 이력, 정산 대사(reconciliation) 전용 신규 스키마.
-- Source of Truth: app/domains/pricing/model.py
--
-- 배경: Gate 3/4(Migration 20260815_01/02)와 동일한 근거 — 이
-- Migration이 만드는 5개 테이블(product_pricings/price_change_
-- requests/price_change_status_events/margin_snapshots/settlement_
-- reconciliations)은 전부 신규다. 실제 homez.db에는 존재하지 않는다
-- (읽기 전용으로 재확인). 기존 테이블(marketplace_settlements 등)의
-- 컬럼은 이 Migration이 건드리지 않는다 — MarketplaceSettlement.status
-- 에 HELD/MISMATCH 값을 새로 쓰는 것은 애플리케이션 레벨의 허용값
-- 확장일 뿐이고, status 컬럼 자체가 이미 제약 없는 VARCHAR(20)이라
-- 스키마 변경이 필요 없다(app/domains/settlement/model.py 참고).
--
-- 1) product_pricings — Listing(marketplace_listings.id) 단위 현재
--    판매가 + 원가/배송비/채널수수료/결제수수료/광고비/반품충당/세금
--    구성 + 가장 최근 계산된 예상 손익 캐시(요구사항 1).
-- 2) price_change_requests / price_change_status_events — 판매가
--    변경 승인 흐름 + append-only 이력(요구사항 3).
-- 3) margin_snapshots — 채널별 예상(EXPECTED)/실제(ACTUAL) 손익
--    append-only 스냅샷(요구사항 2).
-- 4) settlement_reconciliations — Order × Settlement 대사 현재상태
--    (요구사항 4/5).
--
-- 이 Migration은 실제 homez.db에 적용하지 않는다. 임시 SQLite 파일
-- (기존 22개 Migration을 순서대로 적용해 재현한 스키마)에서만
-- 검증했다(tests/test_gate5_pricing_settlement_migration.py).

BEGIN;

-- ====================================================
-- 1) product_pricings
-- ====================================================

CREATE TABLE product_pricings (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	listing_id INTEGER NOT NULL,
	current_sale_price NUMERIC(14, 2) NOT NULL,
	cost_of_goods NUMERIC(14, 2) NOT NULL,
	shipping_cost NUMERIC(14, 2) NOT NULL,
	packaging_cost NUMERIC(14, 2) NOT NULL,
	ad_cost NUMERIC(14, 2) NOT NULL,
	channel_fee_rate NUMERIC(6, 4) NOT NULL,
	payment_fee_rate NUMERIC(6, 4) NOT NULL,
	return_reserve_rate NUMERIC(6, 4) NOT NULL,
	tax_basis_rate NUMERIC(6, 4) NOT NULL,
	expected_revenue NUMERIC(14, 2) NOT NULL,
	expected_total_cost NUMERIC(14, 2) NOT NULL,
	expected_margin_amount NUMERIC(14, 2) NOT NULL,
	expected_margin_rate NUMERIC(6, 4) NOT NULL,
	expected_break_even_price NUMERIC(14, 2),
	pending_price_change_id INTEGER,
	version INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_product_pricings_company_listing UNIQUE (company_id, listing_id)
);

CREATE INDEX ix_product_pricings_id ON product_pricings (id);
CREATE INDEX ix_product_pricings_listing_id ON product_pricings (listing_id);
CREATE INDEX ix_product_pricings_company_id ON product_pricings (company_id);

-- ====================================================
-- 2) price_change_requests / price_change_status_events
-- ====================================================

CREATE TABLE price_change_requests (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	listing_id INTEGER NOT NULL,
	product_pricing_id INTEGER NOT NULL,
	previous_sale_price NUMERIC(14, 2) NOT NULL,
	requested_sale_price NUMERIC(14, 2) NOT NULL,
	request_fingerprint VARCHAR(64) NOT NULL,
	reason VARCHAR(500),
	status VARCHAR(20) NOT NULL,
	requested_by INTEGER NOT NULL,
	requested_at DATETIME NOT NULL,
	decided_by INTEGER,
	decided_at DATETIME,
	decision_reason VARCHAR(500),
	idempotency_key VARCHAR(160) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_price_change_requests_company_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX ix_price_change_requests_status ON price_change_requests (status);
CREATE INDEX ix_price_change_requests_listing_id ON price_change_requests (listing_id);
CREATE INDEX ix_price_change_requests_company_id ON price_change_requests (company_id);
CREATE INDEX ix_price_change_requests_product_pricing_id ON price_change_requests (product_pricing_id);
CREATE INDEX ix_price_change_requests_id ON price_change_requests (id);

CREATE TABLE price_change_status_events (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	price_change_request_id INTEGER NOT NULL,
	listing_id INTEGER NOT NULL,
	previous_status VARCHAR(20),
	new_status VARCHAR(20) NOT NULL,
	sale_price_snapshot NUMERIC(14, 2) NOT NULL,
	reason VARCHAR(500),
	actor_user_id INTEGER,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_price_change_status_events_listing_id ON price_change_status_events (listing_id);
CREATE INDEX ix_price_change_status_events_id ON price_change_status_events (id);
CREATE INDEX ix_price_change_status_events_price_change_request_id ON price_change_status_events (price_change_request_id);
CREATE INDEX ix_price_change_status_events_created_at ON price_change_status_events (created_at);
CREATE INDEX ix_price_change_status_events_company_id ON price_change_status_events (company_id);

-- ====================================================
-- 3) margin_snapshots
-- ====================================================

CREATE TABLE margin_snapshots (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	listing_id INTEGER NOT NULL,
	margin_type VARCHAR(10) NOT NULL,
	order_id INTEGER,
	settlement_id INTEGER,
	reason VARCHAR(30) NOT NULL,
	quantity_basis INTEGER NOT NULL,
	revenue NUMERIC(14, 2) NOT NULL,
	cost_of_goods NUMERIC(14, 2) NOT NULL,
	channel_fee NUMERIC(14, 2) NOT NULL,
	payment_fee NUMERIC(14, 2) NOT NULL,
	shipping_cost NUMERIC(14, 2) NOT NULL,
	packaging_cost NUMERIC(14, 2) NOT NULL,
	ad_cost NUMERIC(14, 2) NOT NULL,
	return_reserve NUMERIC(14, 2) NOT NULL,
	tax NUMERIC(14, 2) NOT NULL,
	refund_adjustment NUMERIC(14, 2) NOT NULL,
	total_cost NUMERIC(14, 2) NOT NULL,
	margin_amount NUMERIC(14, 2) NOT NULL,
	margin_rate NUMERIC(6, 4) NOT NULL,
	estimated_components_json TEXT NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_margin_snapshots_order_id ON margin_snapshots (order_id);
CREATE INDEX ix_margin_snapshots_listing_id ON margin_snapshots (listing_id);
CREATE INDEX ix_margin_snapshots_id ON margin_snapshots (id);
CREATE INDEX ix_margin_snapshots_settlement_id ON margin_snapshots (settlement_id);
CREATE INDEX ix_margin_snapshots_margin_type ON margin_snapshots (margin_type);
CREATE INDEX ix_margin_snapshots_company_id ON margin_snapshots (company_id);
CREATE INDEX ix_margin_snapshots_created_at ON margin_snapshots (created_at);

-- ====================================================
-- 4) settlement_reconciliations
-- ====================================================

CREATE TABLE settlement_reconciliations (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	order_id INTEGER NOT NULL,
	settlement_id INTEGER,
	expected_net_amount NUMERIC(14, 2) NOT NULL,
	actual_net_amount NUMERIC(14, 2),
	variance_amount NUMERIC(14, 2),
	refund_amount NUMERIC(14, 2) NOT NULL,
	status VARCHAR(20) NOT NULL,
	notes VARCHAR(1000),
	reconciled_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_settlement_reconciliations_company_order UNIQUE (company_id, order_id)
);

CREATE INDEX ix_settlement_reconciliations_company_id ON settlement_reconciliations (company_id);
CREATE INDEX ix_settlement_reconciliations_id ON settlement_reconciliations (id);
CREATE INDEX ix_settlement_reconciliations_status ON settlement_reconciliations (status);
CREATE INDEX ix_settlement_reconciliations_order_id ON settlement_reconciliations (order_id);
CREATE INDEX ix_settlement_reconciliations_settlement_id ON settlement_reconciliations (settlement_id);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로
-- 실행. 전부 신규 테이블이므로 단순 DROP으로 완전히 원복된다):
-- BEGIN;
-- DROP TABLE settlement_reconciliations;
-- DROP TABLE margin_snapshots;
-- DROP TABLE price_change_status_events;
-- DROP TABLE price_change_requests;
-- DROP TABLE product_pricings;
-- COMMIT;
