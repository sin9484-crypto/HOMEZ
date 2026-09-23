-- =========================================================
-- Homez OS
--
-- File : migrations/20260921_00_create_supplier_option_link_schema.sql
--
-- 2026-09-21 옵션 연결 — 쿠팡에 등록한 판매 옵션(회사 + 쿠팡 스토어 연결 +
-- externalVendorSku)과 실제로 매입할 공급처(온채널) 상품코드·옵션ID의
-- 영구 대응 테이블 1개. 기존 테이블·데이터는 전혀 건드리지 않는다(추가형).
-- HOMEZ 재고 SKU와의 관계는 기존 order_sku_resolutions(같은 조인 키)로
-- 이어지므로 여기에 중복 저장하지 않는다. 비밀값·고객 개인정보 없음.
-- =========================================================

BEGIN;

CREATE TABLE supplier_option_links (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	store_connection_id INTEGER NOT NULL,
	channel_sku VARCHAR(150) NOT NULL,
	purchase_connection_id INTEGER NOT NULL,
	supplier_product_code VARCHAR(50) NOT NULL,
	supplier_option_id VARCHAR(50) NOT NULL,
	supplier_option_name_snapshot VARCHAR(200),
	units_per_sale INTEGER NOT NULL,
	status VARCHAR(20) NOT NULL,
	status_reason VARCHAR(200),
	coupang_seller_product_id VARCHAR(50),
	coupang_vendor_item_id VARCHAR(50),
	version INTEGER NOT NULL,
	confirmed_by INTEGER NOT NULL,
	confirmed_at DATETIME NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_supplier_option_link_scope UNIQUE (company_id, store_connection_id, channel_sku),
	CONSTRAINT uq_supplier_option_link_vendor_item UNIQUE (company_id, store_connection_id, coupang_vendor_item_id),
	CONSTRAINT ck_supplier_option_link_units CHECK (units_per_sale >= 1)
);

CREATE INDEX ix_supplier_option_links_id ON supplier_option_links (id);
CREATE INDEX ix_supplier_option_links_status ON supplier_option_links (status);
CREATE INDEX ix_supplier_option_links_store_connection_id ON supplier_option_links (store_connection_id);
CREATE INDEX ix_supplier_option_links_purchase_connection_id ON supplier_option_links (purchase_connection_id);
CREATE INDEX ix_supplier_option_links_company_id ON supplier_option_links (company_id);

COMMIT;

-- Rollback(수동, 실제 DB 적용 시 별도 승인 후에만 사용):
-- DROP TABLE IF EXISTS supplier_option_links;
