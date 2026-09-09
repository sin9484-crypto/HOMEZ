-- Purpose: HOMEZ V2.3 Marketplace Settlement Hardening — 전체 스키마 생성.
-- Source of Truth: app/domains/funding/model.py, app/domains/settlement/model.py
--
-- 대상 5개 테이블 (Model 기준, ForeignKey 없음 — 전부 논리 참조 컬럼):
--   funding_accounts
--   funding_ledgers
--   funding_holds
--   supplier_payments
--   marketplace_settlements
--
-- 실행 전 필수 확인: 아래 5개 테이블이 대상 DB에 하나도 존재하지 않아야 한다.
-- 일부만 존재하는 상태(부분 적용)를 이 스크립트는 감지하지 않고 그대로
-- CREATE TABLE을 시도해 실패하도록 둔다(IF NOT EXISTS를 쓰지 않음).
--
-- funding_ledgers에는 Marketplace Settlement 전용 부분 유일 인덱스
-- (uq_funding_ledger_settlement_type)가 Model.__table_args__에 정의되어
-- 있으므로 이 스크립트에서 함께 생성한다. 기존 파일
-- migrations/20260727_add_settlement_ledger_unique_index.sql은
-- CREATE UNIQUE INDEX IF NOT EXISTS를 사용하므로, 이 스크립트가 먼저
-- 적용된 뒤에 실행되어도 동일 인덱스가 이미 존재해 아무 동작도 하지
-- 않는(no-op) 안전한 후속 안전장치로만 남는다. 그 파일은 수정하지 않는다.

BEGIN;

CREATE TABLE funding_accounts (
	id INTEGER NOT NULL,
	company_id INTEGER,
	total_funding FLOAT NOT NULL,
	held_amount FLOAT NOT NULL,
	currency VARCHAR(10) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_funding_accounts_id ON funding_accounts (id);
CREATE UNIQUE INDEX ix_funding_accounts_company_id ON funding_accounts (company_id);

CREATE TABLE funding_ledgers (
	id INTEGER NOT NULL,
	account_id INTEGER NOT NULL,
	amount FLOAT NOT NULL,
	type VARCHAR(30) NOT NULL,
	reference_type VARCHAR(50),
	reference_id INTEGER,
	memo VARCHAR(500),
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_funding_ledgers_type ON funding_ledgers (type);
CREATE INDEX ix_funding_ledgers_id ON funding_ledgers (id);
CREATE INDEX ix_funding_ledgers_account_id ON funding_ledgers (account_id);
CREATE UNIQUE INDEX uq_funding_ledger_settlement_type ON funding_ledgers (reference_id, type) WHERE reference_type = 'settlement';

CREATE TABLE funding_holds (
	id INTEGER NOT NULL,
	account_id INTEGER NOT NULL,
	order_id INTEGER NOT NULL,
	purchase_id INTEGER,
	amount FLOAT NOT NULL,
	status VARCHAR(20) NOT NULL,
	idempotency_key VARCHAR(100) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_funding_holds_idempotency UNIQUE (idempotency_key)
);

CREATE INDEX ix_funding_holds_purchase_id ON funding_holds (purchase_id);
CREATE INDEX ix_funding_holds_order_id ON funding_holds (order_id);
CREATE INDEX ix_funding_holds_account_id ON funding_holds (account_id);
CREATE INDEX ix_funding_holds_id ON funding_holds (id);
CREATE INDEX ix_funding_holds_status ON funding_holds (status);

CREATE TABLE supplier_payments (
	id INTEGER NOT NULL,
	purchase_id INTEGER NOT NULL,
	order_id INTEGER NOT NULL,
	supplier_id INTEGER NOT NULL,
	account_id INTEGER NOT NULL,
	hold_id INTEGER NOT NULL,
	amount FLOAT NOT NULL,
	status VARCHAR(20) NOT NULL,
	paid_at DATETIME NOT NULL,
	idempotency_key VARCHAR(100) NOT NULL,
	memo VARCHAR(500),
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_supplier_payments_idempotency UNIQUE (idempotency_key),
	CONSTRAINT uq_supplier_payments_purchase UNIQUE (purchase_id)
);

CREATE INDEX ix_supplier_payments_supplier_id ON supplier_payments (supplier_id);
CREATE INDEX ix_supplier_payments_status ON supplier_payments (status);
CREATE INDEX ix_supplier_payments_order_id ON supplier_payments (order_id);
CREATE INDEX ix_supplier_payments_account_id ON supplier_payments (account_id);
CREATE INDEX ix_supplier_payments_purchase_id ON supplier_payments (purchase_id);
CREATE INDEX ix_supplier_payments_id ON supplier_payments (id);
CREATE INDEX ix_supplier_payments_hold_id ON supplier_payments (hold_id);

CREATE TABLE marketplace_settlements (
	id INTEGER NOT NULL,
	market VARCHAR(30) NOT NULL,
	market_order_id VARCHAR(100) NOT NULL,
	order_id INTEGER,
	account_id INTEGER NOT NULL,
	gross_amount FLOAT NOT NULL,
	fee_amount FLOAT NOT NULL,
	net_amount FLOAT NOT NULL,
	status VARCHAR(20) NOT NULL,
	deposited_at DATETIME,
	funding_ledger_id INTEGER,
	idempotency_key VARCHAR(120) NOT NULL,
	memo VARCHAR(500),
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_marketplace_settlements_idempotency UNIQUE (idempotency_key)
);

CREATE INDEX ix_marketplace_settlements_market ON marketplace_settlements (market);
CREATE INDEX ix_marketplace_settlements_account_id ON marketplace_settlements (account_id);
CREATE INDEX ix_marketplace_settlements_market_order_id ON marketplace_settlements (market_order_id);
CREATE INDEX ix_marketplace_settlements_status ON marketplace_settlements (status);
CREATE INDEX ix_marketplace_settlements_id ON marketplace_settlements (id);
CREATE INDEX ix_marketplace_settlements_order_id ON marketplace_settlements (order_id);

COMMIT;

-- Rollback (수동 실행 전용, 이 파일 자체에서는 실행하지 않음):
-- 테이블 생성 역순으로 DROP한다.
--
-- BEGIN;
-- DROP INDEX IF EXISTS ix_marketplace_settlements_order_id;
-- DROP INDEX IF EXISTS ix_marketplace_settlements_id;
-- DROP INDEX IF EXISTS ix_marketplace_settlements_status;
-- DROP INDEX IF EXISTS ix_marketplace_settlements_market_order_id;
-- DROP INDEX IF EXISTS ix_marketplace_settlements_account_id;
-- DROP INDEX IF EXISTS ix_marketplace_settlements_market;
-- DROP TABLE IF EXISTS marketplace_settlements;
--
-- DROP INDEX IF EXISTS ix_supplier_payments_hold_id;
-- DROP INDEX IF EXISTS ix_supplier_payments_id;
-- DROP INDEX IF EXISTS ix_supplier_payments_purchase_id;
-- DROP INDEX IF EXISTS ix_supplier_payments_account_id;
-- DROP INDEX IF EXISTS ix_supplier_payments_order_id;
-- DROP INDEX IF EXISTS ix_supplier_payments_status;
-- DROP INDEX IF EXISTS ix_supplier_payments_supplier_id;
-- DROP TABLE IF EXISTS supplier_payments;
--
-- DROP INDEX IF EXISTS ix_funding_holds_status;
-- DROP INDEX IF EXISTS ix_funding_holds_id;
-- DROP INDEX IF EXISTS ix_funding_holds_account_id;
-- DROP INDEX IF EXISTS ix_funding_holds_order_id;
-- DROP INDEX IF EXISTS ix_funding_holds_purchase_id;
-- DROP TABLE IF EXISTS funding_holds;
--
-- DROP INDEX IF EXISTS uq_funding_ledger_settlement_type;
-- DROP INDEX IF EXISTS ix_funding_ledgers_account_id;
-- DROP INDEX IF EXISTS ix_funding_ledgers_id;
-- DROP INDEX IF EXISTS ix_funding_ledgers_type;
-- DROP TABLE IF EXISTS funding_ledgers;
--
-- DROP INDEX IF EXISTS ix_funding_accounts_company_id;
-- DROP INDEX IF EXISTS ix_funding_accounts_id;
-- DROP TABLE IF EXISTS funding_accounts;
-- COMMIT;
