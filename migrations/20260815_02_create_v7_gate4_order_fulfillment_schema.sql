-- Purpose: V7 Gate 4(2026-08-15) — 주문 수집/재고 예약/발주·입고/
-- 배송(부분출고)/반품·교환 전체 흐름.
-- Source of Truth: app/domains/order/model.py, app/domains/purchase/
-- model.py, app/domains/shipment/model.py, app/domains/return_order/
-- model.py
--
-- 배경: Gate 3(Migration 20260815_01)와 동일한 근거 — 기존 pre-pivot
-- `app/domains/order`/`purchase`/`shipment`(products/suppliers FK
-- 참조, main.py에 router import는 있었으나 include_router()는 주석
-- 처리되어 실제로 마운트된 적이 없었음)와 완전히 빈 스캐폴딩이던
-- `app/domains/return_order`를 전부 대체/신규 구현한다. 실제
-- homez.db에는 orders/purchases/shipments/order_items/return_orders
-- 테이블이 전혀 존재하지 않는다(읽기 전용으로 재확인) — 전부 신규
-- CREATE TABLE이므로 재생성 기법(_new → DROP → RENAME)이 필요 없다.
--
-- 1) orders / order_items / order_ingestion_events / order_status_events
--    — 회사 스코프 채널 주문 수집(정규화 + 원본/정규화 분리 저장) +
--    품목별 Inventory 예약 연결 + append-only 상태 이력.
-- 2) purchases / purchase_items — 공급처 발주(Funding Hold 연결) +
--    품목 라인.
-- 3) shipments / shipment_items / shipment_status_events — 송장(부분
--    출고 지원 — 품목 단위로 여러 송장에 분산) + append-only 상태
--    이력.
-- 4) return_orders / return_order_status_events — 반품/교환 + append-
--    only 상태 이력.
--
-- 이 Migration은 실제 homez.db에 적용하지 않는다. 임시 SQLite 파일
-- (기존 20개 Migration을 순서대로 적용해 재현한 스키마)에서만
-- 검증했다(tests/test_gate4_order_fulfillment_migration.py).

BEGIN;

-- ====================================================
-- 1) orders
-- ====================================================

CREATE TABLE orders (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	channel_code VARCHAR(30) NOT NULL,
	channel_order_id VARCHAR(150) NOT NULL,
	order_number VARCHAR(100) NOT NULL,
	status VARCHAR(30) NOT NULL,
	buyer_name VARCHAR(100) NOT NULL,
	receiver_name VARCHAR(100) NOT NULL,
	receiver_phone VARCHAR(50) NOT NULL,
	receiver_address VARCHAR(500) NOT NULL,
	receiver_zipcode VARCHAR(20) NOT NULL,
	total_amount FLOAT NOT NULL,
	ordered_at DATETIME NOT NULL,
	channel_sync_status VARCHAR(20) NOT NULL,
	channel_last_synced_at DATETIME,
	channel_last_sync_error VARCHAR(200),
	rate_limit_retry_after_seconds INTEGER,
	rate_limit_retry_available_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_orders_company_channel_order UNIQUE (company_id, channel_code, channel_order_id)
);

CREATE INDEX ix_orders_id ON orders (id);
CREATE INDEX ix_orders_company_id ON orders (company_id);
CREATE INDEX ix_orders_channel_code ON orders (channel_code);
CREATE INDEX ix_orders_status ON orders (status);

-- ====================================================
-- 2) order_items
-- ====================================================

CREATE TABLE order_items (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	order_id INTEGER NOT NULL,
	inventory_sku_id INTEGER NOT NULL,
	channel_sku VARCHAR(150) NOT NULL,
	sku_code_snapshot VARCHAR(100) NOT NULL,
	product_name_snapshot VARCHAR(300) NOT NULL,
	quantity INTEGER NOT NULL,
	unit_price FLOAT NOT NULL,
	status VARCHAR(20) NOT NULL,
	reservation_id INTEGER,
	purchase_id INTEGER,
	shipped_quantity INTEGER NOT NULL,
	returned_quantity INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_order_items_id ON order_items (id);
CREATE INDEX ix_order_items_company_id ON order_items (company_id);
CREATE INDEX ix_order_items_order_id ON order_items (order_id);
CREATE INDEX ix_order_items_inventory_sku_id ON order_items (inventory_sku_id);
CREATE INDEX ix_order_items_status ON order_items (status);
CREATE INDEX ix_order_items_reservation_id ON order_items (reservation_id);
CREATE INDEX ix_order_items_purchase_id ON order_items (purchase_id);

-- ====================================================
-- 3) order_ingestion_events (append-only)
-- ====================================================

CREATE TABLE order_ingestion_events (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	channel_code VARCHAR(30) NOT NULL,
	channel_order_id VARCHAR(150) NOT NULL,
	status VARCHAR(20) NOT NULL,
	order_id INTEGER,
	raw_payload TEXT NOT NULL,
	normalized_snapshot TEXT,
	error_code VARCHAR(50),
	error_summary VARCHAR(500),
	triggered_by INTEGER,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_order_ingestion_events_id ON order_ingestion_events (id);
CREATE INDEX ix_order_ingestion_events_company_id ON order_ingestion_events (company_id);
CREATE INDEX ix_order_ingestion_events_channel_code ON order_ingestion_events (channel_code);
CREATE INDEX ix_order_ingestion_events_channel_order_id ON order_ingestion_events (channel_order_id);
CREATE INDEX ix_order_ingestion_events_status ON order_ingestion_events (status);
CREATE INDEX ix_order_ingestion_events_order_id ON order_ingestion_events (order_id);

-- ====================================================
-- 4) order_status_events (append-only)
-- ====================================================

CREATE TABLE order_status_events (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	order_id INTEGER NOT NULL,
	previous_status VARCHAR(30),
	new_status VARCHAR(30) NOT NULL,
	source VARCHAR(30) NOT NULL,
	reason VARCHAR(500),
	triggered_by INTEGER,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_order_status_events_id ON order_status_events (id);
CREATE INDEX ix_order_status_events_company_id ON order_status_events (company_id);
CREATE INDEX ix_order_status_events_order_id ON order_status_events (order_id);

-- ====================================================
-- 5) purchases
-- ====================================================

CREATE TABLE purchases (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	order_id INTEGER NOT NULL,
	supplier_id INTEGER NOT NULL,
	status VARCHAR(20) NOT NULL,
	idempotency_key VARCHAR(150) NOT NULL,
	total_cost FLOAT NOT NULL,
	supplier_order_number VARCHAR(100),
	memo VARCHAR(1000),
	requested_at DATETIME NOT NULL,
	confirmed_at DATETIME,
	received_at DATETIME,
	cancelled_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_purchases_company_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX ix_purchases_id ON purchases (id);
CREATE INDEX ix_purchases_company_id ON purchases (company_id);
CREATE INDEX ix_purchases_order_id ON purchases (order_id);
CREATE INDEX ix_purchases_supplier_id ON purchases (supplier_id);
CREATE INDEX ix_purchases_status ON purchases (status);

-- ====================================================
-- 6) purchase_items
-- ====================================================

CREATE TABLE purchase_items (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	purchase_id INTEGER NOT NULL,
	order_item_id INTEGER NOT NULL,
	inventory_sku_id INTEGER NOT NULL,
	quantity INTEGER NOT NULL,
	unit_cost FLOAT NOT NULL,
	subtotal_cost FLOAT NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_purchase_items_id ON purchase_items (id);
CREATE INDEX ix_purchase_items_company_id ON purchase_items (company_id);
CREATE INDEX ix_purchase_items_purchase_id ON purchase_items (purchase_id);
CREATE INDEX ix_purchase_items_order_item_id ON purchase_items (order_item_id);
CREATE INDEX ix_purchase_items_inventory_sku_id ON purchase_items (inventory_sku_id);

-- ====================================================
-- 7) shipments
-- ====================================================

CREATE TABLE shipments (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	order_id INTEGER NOT NULL,
	shipment_number VARCHAR(100) NOT NULL,
	idempotency_key VARCHAR(150) NOT NULL,
	status VARCHAR(30) NOT NULL,
	courier VARCHAR(100),
	invoice_number VARCHAR(100),
	tracking_url VARCHAR(500),
	shipped_at DATETIME,
	delivered_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_shipments_company_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX ix_shipments_id ON shipments (id);
CREATE INDEX ix_shipments_company_id ON shipments (company_id);
CREATE INDEX ix_shipments_order_id ON shipments (order_id);
CREATE INDEX ix_shipments_status ON shipments (status);
CREATE INDEX ix_shipments_invoice_number ON shipments (invoice_number);

-- ====================================================
-- 8) shipment_items
-- ====================================================

CREATE TABLE shipment_items (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	shipment_id INTEGER NOT NULL,
	order_item_id INTEGER NOT NULL,
	reservation_id INTEGER NOT NULL,
	quantity INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_shipment_items_id ON shipment_items (id);
CREATE INDEX ix_shipment_items_company_id ON shipment_items (company_id);
CREATE INDEX ix_shipment_items_shipment_id ON shipment_items (shipment_id);
CREATE INDEX ix_shipment_items_order_item_id ON shipment_items (order_item_id);
CREATE INDEX ix_shipment_items_reservation_id ON shipment_items (reservation_id);

-- ====================================================
-- 9) shipment_status_events (append-only)
-- ====================================================

CREATE TABLE shipment_status_events (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	shipment_id INTEGER NOT NULL,
	previous_status VARCHAR(30),
	new_status VARCHAR(30) NOT NULL,
	reason VARCHAR(500),
	triggered_by INTEGER,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_shipment_status_events_id ON shipment_status_events (id);
CREATE INDEX ix_shipment_status_events_company_id ON shipment_status_events (company_id);
CREATE INDEX ix_shipment_status_events_shipment_id ON shipment_status_events (shipment_id);

-- ====================================================
-- 10) return_orders
-- ====================================================

CREATE TABLE return_orders (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	order_id INTEGER NOT NULL,
	order_item_id INTEGER NOT NULL,
	shipment_id INTEGER NOT NULL,
	return_type VARCHAR(20) NOT NULL,
	status VARCHAR(20) NOT NULL,
	quantity INTEGER NOT NULL,
	reason VARCHAR(500) NOT NULL,
	idempotency_key VARCHAR(150) NOT NULL,
	requested_at DATETIME NOT NULL,
	approved_at DATETIME,
	received_at DATETIME,
	completed_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_return_orders_company_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX ix_return_orders_id ON return_orders (id);
CREATE INDEX ix_return_orders_company_id ON return_orders (company_id);
CREATE INDEX ix_return_orders_order_id ON return_orders (order_id);
CREATE INDEX ix_return_orders_order_item_id ON return_orders (order_item_id);
CREATE INDEX ix_return_orders_shipment_id ON return_orders (shipment_id);
CREATE INDEX ix_return_orders_return_type ON return_orders (return_type);
CREATE INDEX ix_return_orders_status ON return_orders (status);

-- ====================================================
-- 11) return_order_status_events (append-only)
-- ====================================================

CREATE TABLE return_order_status_events (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	return_order_id INTEGER NOT NULL,
	previous_status VARCHAR(20),
	new_status VARCHAR(20) NOT NULL,
	reason VARCHAR(500),
	triggered_by INTEGER,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_return_order_status_events_id ON return_order_status_events (id);
CREATE INDEX ix_return_order_status_events_company_id ON return_order_status_events (company_id);
CREATE INDEX ix_return_order_status_events_return_order_id ON return_order_status_events (return_order_id);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로
-- 실행. 전부 신규 테이블이므로 단순 DROP으로 완전히 원복된다):
-- BEGIN;
-- DROP TABLE return_order_status_events;
-- DROP TABLE return_orders;
-- DROP TABLE shipment_status_events;
-- DROP TABLE shipment_items;
-- DROP TABLE shipments;
-- DROP TABLE purchase_items;
-- DROP TABLE purchases;
-- DROP TABLE order_status_events;
-- DROP TABLE order_ingestion_events;
-- DROP TABLE order_items;
-- DROP TABLE orders;
-- COMMIT;
