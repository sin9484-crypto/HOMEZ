-- Purpose: V7 Section F(2026-08-20) — 공급처 검색·상품 연결.
-- Source of Truth: app/domains/source/model.py
--
-- 배경: 기존 발주(Purchase, Migration 20260815_02)는 이미 supplier_id를
-- 알고 있다고 가정한다(호출자가 미리 정해서 넘김). 이 파일은 그
-- supplier_id를 실제로 "찾아 연결"하는 상류 단계 테이블 1개
-- (supplier_product_links)만 새로 만든다. 전역 공급처 카탈로그
-- (suppliers, 이미 존재)는 그대로 재사용하고 새 Supplier 테이블은
-- 만들지 않는다(2026-08-20 CTO 결정 — Supplier는 전역 공유 카탈로그로
-- 유지).
--
-- 이 Migration은 실제 homez.db에 적용하지 않는다. 임시 SQLite 파일
-- (기존 Migration을 순서대로 적용해 재현한 스키마)에서만 검증했다
-- (tests/test_source_supplier_product_links_migration.py).

BEGIN;

CREATE TABLE supplier_product_links (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	product_candidate_id INTEGER NOT NULL,
	supplier_id INTEGER NOT NULL,
	supplier_sku VARCHAR(150) NOT NULL,
	unit_cost FLOAT NOT NULL,
	moq INTEGER NOT NULL,
	lead_time_days INTEGER,
	shipping_cost FLOAT,
	price_valid_until DATETIME,
	stock_available INTEGER,
	return_policy VARCHAR(500),
	status VARCHAR(20) NOT NULL,
	created_by INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_supplier_product_links_company_candidate_supplier_sku
		UNIQUE (company_id, product_candidate_id, supplier_id, supplier_sku)
);

CREATE INDEX ix_supplier_product_links_id ON supplier_product_links (id);
CREATE INDEX ix_supplier_product_links_company_id ON supplier_product_links (company_id);
CREATE INDEX ix_supplier_product_links_product_candidate_id ON supplier_product_links (product_candidate_id);
CREATE INDEX ix_supplier_product_links_supplier_id ON supplier_product_links (supplier_id);
CREATE INDEX ix_supplier_product_links_status ON supplier_product_links (status);

COMMIT;

-- Rollback(주석 전용, 자동 실행되지 않음):
-- DROP TABLE supplier_product_links;
