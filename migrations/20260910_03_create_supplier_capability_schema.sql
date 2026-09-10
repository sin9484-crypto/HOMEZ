-- Purpose: HOMEZ 사용자 운영 기준(HOMEZ_USER_OPERATION_SETTINGS.md)
-- 7번 — "국내·해외 매입처 Adapter 계약을 통일하고, 상품/옵션/가격/
-- 재고/배송비/배송기간/발주가능/취소가능 여부를 능력 플래그로
-- 노출한다. 미확인 기능은 '미확인'으로 표시하고 추측하지 않는다."
-- Phase 9 구현.
-- Source of Truth: app/domains/supplier_capability/model.py
--                   (SupplierProfile, SupplierCapabilityRecord),
--                   app/domains/supplier_capability/constants.py
--                   (SupplierCapabilityFlag, CapabilitySupport)
--
-- 이 Migration은 기존 4개 Adapter 계약(purchase_task/channel_adapter,
-- source/discovery_providers, purchase/supplier_order_providers,
-- retail_purchase/provider)의 어떤 테이블도 건드리지 않는다 — 이유는
-- app/domains/supplier_capability/constants.py 상단 주석 참고
-- (대규모 리팩터링 회피, 추가 전용 설계).
--
-- supplier_id는 논리 참조(suppliers.id, FK 없음)다.
--
-- 완전히 새로운 테이블만 추가한다(ALTER 없음) — 기존 테이블은 전혀
-- 건드리지 않는다.

BEGIN;

CREATE TABLE supplier_profiles (
    id INTEGER NOT NULL PRIMARY KEY,
    supplier_id INTEGER NOT NULL,
    is_international BOOLEAN NOT NULL DEFAULT 0,
    country_code VARCHAR(2),
    default_currency VARCHAR(10) NOT NULL DEFAULT 'KRW',
    consignment_direct_to_customer BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE (supplier_id)
);

CREATE INDEX ix_supplier_profiles_id ON supplier_profiles (id);
CREATE INDEX ix_supplier_profiles_supplier_id ON supplier_profiles (supplier_id);

CREATE TABLE supplier_capability_records (
    id INTEGER NOT NULL PRIMARY KEY,
    supplier_id INTEGER NOT NULL,
    capability VARCHAR(30) NOT NULL,
    support VARCHAR(20) NOT NULL DEFAULT 'UNKNOWN',
    note VARCHAR(500),
    checked_by INTEGER,
    checked_at DATETIME NOT NULL,
    UNIQUE (supplier_id, capability)
);

CREATE INDEX ix_supplier_capability_records_id ON supplier_capability_records (id);
CREATE INDEX ix_supplier_capability_records_supplier_id ON supplier_capability_records (supplier_id);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로
-- 실행. 완전히 새로운 테이블이라 DROP TABLE로 데이터 손실 없이
-- 되돌릴 수 있다):
-- BEGIN;
-- DROP TABLE supplier_capability_records;
-- DROP TABLE supplier_profiles;
-- COMMIT;
