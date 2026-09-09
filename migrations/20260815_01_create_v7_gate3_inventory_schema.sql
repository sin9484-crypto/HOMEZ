-- Purpose: V7 Gate 3(2026-08-15) — Inventory 핵심(SKU/옵션/채널매핑,
-- 가용/예약/안전재고 + append-only 원장, 원자적 예약/해제/확정소모/
-- 조정).
-- Source of Truth: app/domains/inventory/model.py
--
-- 배경(CTO 지시 원문 요구사항 1~8, 상세는
-- docs/HOMEZ_V7_INVENTORY_PLAN.md 2/10절): 기존 app/domains/inventory
-- (pre-pivot, products/suppliers FK 참조, main.py 미마운트)를 대체하는
-- 완전히 새로운 4개 테이블을 신설한다. 기존 테이블(orders/purchases 등)
-- 은 이번 Migration의 대상이 아니다 — Order/Purchase 도메인 자체가
-- 아직 실제 DB에 존재하지 않는다(V7 Gate 4 범위).
--
-- 1) inventory_skus — 회사 스코프 SKU(옵션 단위). 승인된
--    ProductCandidate 하나가 여러 옵션(색상/사이즈 등)을 가질 수
--    있다는 요구사항의 "재고 관점" 표현. available_qty/reserved_qty는
--    현재상태(FundingAccount와 동일 철학, mutate).
-- 2) inventory_reservations — 예약 상태 머신(RESERVED/RELEASED/
--    CONSUMED, FundingHold와 동일 철학). idempotency_key는
--    (company_id, idempotency_key) 복합 UNIQUE.
-- 3) inventory_ledger_events — append-only 원장(FundingLedger와 동일
--    철학). idempotency_key는 NULL이 아닌 행에만 적용되는 부분 유일
--    인덱스(RESERVED/RELEASED/CONSUMED는 예약 자체가 idempotency를
--    담당하므로 NULL, RESTOCKED/ADJUSTED/CHANNEL_SYNC만 이 컬럼을
--    직접 사용).
-- 4) inventory_channel_mappings — SKU × 판매채널 매핑. 새 채널/계정
--    개념을 만들지 않고 marketplace_listings.id에 대한 논리 참조만
--    추가한다(중복 구현 금지 지시 준수).
--
-- 전부 신규 CREATE TABLE이므로 재생성 기법(_new → DROP → RENAME)이
-- 필요 없다 — 기존 데이터 영향 없음(신규 테이블은 항상 0행에서
-- 시작한다).
--
-- 이 Migration은 실제 homez.db에 적용하지 않는다. 임시 SQLite 파일
-- (기존 19개 Migration을 순서대로 적용해 재현한 스키마)에서만
-- 검증했다(tests/test_gate3_inventory_migration.py).

BEGIN;

-- ====================================================
-- 1) inventory_skus
-- ====================================================

CREATE TABLE inventory_skus (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	product_candidate_id INTEGER NOT NULL,
	sku_code VARCHAR(100) NOT NULL,
	option_label VARCHAR(200) NOT NULL,
	available_qty INTEGER NOT NULL,
	reserved_qty INTEGER NOT NULL,
	safety_stock INTEGER NOT NULL,
	is_active BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_inventory_skus_company_sku_code UNIQUE (company_id, sku_code),
	CONSTRAINT uq_inventory_skus_company_candidate_option UNIQUE (company_id, product_candidate_id, option_label)
);

CREATE INDEX ix_inventory_skus_id ON inventory_skus (id);
CREATE INDEX ix_inventory_skus_company_id ON inventory_skus (company_id);
CREATE INDEX ix_inventory_skus_product_candidate_id ON inventory_skus (product_candidate_id);

-- ====================================================
-- 2) inventory_reservations
-- ====================================================

CREATE TABLE inventory_reservations (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	inventory_sku_id INTEGER NOT NULL,
	quantity INTEGER NOT NULL,
	status VARCHAR(20) NOT NULL,
	reference_type VARCHAR(50),
	reference_id INTEGER,
	idempotency_key VARCHAR(150) NOT NULL,
	triggered_by INTEGER,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_inventory_reservations_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX ix_inventory_reservations_id ON inventory_reservations (id);
CREATE INDEX ix_inventory_reservations_company_id ON inventory_reservations (company_id);
CREATE INDEX ix_inventory_reservations_inventory_sku_id ON inventory_reservations (inventory_sku_id);
CREATE INDEX ix_inventory_reservations_status ON inventory_reservations (status);
CREATE INDEX ix_inventory_reservations_reference_id ON inventory_reservations (reference_id);

-- ====================================================
-- 3) inventory_ledger_events (append-only)
-- ====================================================

CREATE TABLE inventory_ledger_events (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	inventory_sku_id INTEGER NOT NULL,
	event_type VARCHAR(30) NOT NULL,
	quantity_delta INTEGER NOT NULL,
	available_after INTEGER NOT NULL,
	reserved_after INTEGER NOT NULL,
	safety_stock_threshold INTEGER NOT NULL,
	channel_code VARCHAR(30),
	reservation_id INTEGER,
	idempotency_key VARCHAR(150),
	reason VARCHAR(500),
	triggered_by INTEGER,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_inventory_ledger_events_id ON inventory_ledger_events (id);
CREATE INDEX ix_inventory_ledger_events_company_id ON inventory_ledger_events (company_id);
CREATE INDEX ix_inventory_ledger_events_inventory_sku_id ON inventory_ledger_events (inventory_sku_id);
CREATE INDEX ix_inventory_ledger_events_event_type ON inventory_ledger_events (event_type);
CREATE INDEX ix_inventory_ledger_events_reservation_id ON inventory_ledger_events (reservation_id);

-- 부분 유일 인덱스 — idempotency_key가 NULL이 아닌 행에만 적용
-- (RESTOCKED/ADJUSTED/CHANNEL_SYNC 전용, RESERVED/RELEASED/CONSUMED는
-- InventoryReservation.idempotency_key가 이미 담당하므로 여기선 항상
-- NULL). FundingLedger.uq_funding_ledger_settlement_type과 동일 기법.
CREATE UNIQUE INDEX uq_inventory_ledger_events_idempotency
	ON inventory_ledger_events (company_id, idempotency_key)
	WHERE idempotency_key IS NOT NULL;

-- ====================================================
-- 4) inventory_channel_mappings
-- ====================================================

CREATE TABLE inventory_channel_mappings (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	inventory_sku_id INTEGER NOT NULL,
	marketplace_listing_id INTEGER NOT NULL,
	channel_code VARCHAR(30) NOT NULL,
	channel_sku VARCHAR(150) NOT NULL,
	is_active BOOLEAN NOT NULL,
	last_sync_status VARCHAR(20) NOT NULL,
	last_synced_at DATETIME,
	last_sync_error TEXT,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_inventory_channel_mappings_sku_listing UNIQUE (company_id, inventory_sku_id, marketplace_listing_id),
	CONSTRAINT uq_inventory_channel_mappings_company_channel_sku UNIQUE (company_id, channel_code, channel_sku)
);

CREATE INDEX ix_inventory_channel_mappings_id ON inventory_channel_mappings (id);
CREATE INDEX ix_inventory_channel_mappings_company_id ON inventory_channel_mappings (company_id);
CREATE INDEX ix_inventory_channel_mappings_inventory_sku_id ON inventory_channel_mappings (inventory_sku_id);
CREATE INDEX ix_inventory_channel_mappings_marketplace_listing_id ON inventory_channel_mappings (marketplace_listing_id);
CREATE INDEX ix_inventory_channel_mappings_channel_code ON inventory_channel_mappings (channel_code);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로
-- 실행. 전부 신규 테이블이므로 단순 DROP으로 완전히 원복된다):
-- BEGIN;
-- DROP TABLE inventory_channel_mappings;
-- DROP TABLE inventory_ledger_events;
-- DROP TABLE inventory_reservations;
-- DROP TABLE inventory_skus;
-- COMMIT;
