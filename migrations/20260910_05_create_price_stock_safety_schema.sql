-- Purpose: HOMEZ 사용자 운영 기준(HOMEZ_USER_OPERATION_SETTINGS.md)
-- 2·7번 — "가격은 주 1회 정기 검토한다", "판매채널 가상재고 기준
-- 이하 시 신규판매 중지." Phase 10 구현.
-- Source of Truth: app/domains/price_stock_safety/model.py
--                   (VirtualStockThreshold, PriceReviewCycleSetting)
--
-- 둘 다 append-only다 — company_id별 가장 최근 행이 현재 값.
-- app/domains/inventory/model.py::InventorySku(실물 창고 재고)는
-- 전혀 참조하지 않는다 — "가상재고"는 그것과 다른 개념이다
-- (model.py 상단 주석 참고).
--
-- 완전히 새로운 테이블만 추가한다(ALTER 없음) — 기존 테이블은 전혀
-- 건드리지 않는다.

BEGIN;

CREATE TABLE virtual_stock_thresholds (
    id INTEGER NOT NULL PRIMARY KEY,
    company_id INTEGER NOT NULL,
    threshold_quantity INTEGER NOT NULL,
    set_by INTEGER NOT NULL,
    set_at DATETIME NOT NULL
);

CREATE INDEX ix_virtual_stock_thresholds_id ON virtual_stock_thresholds (id);
CREATE INDEX ix_virtual_stock_thresholds_company_id ON virtual_stock_thresholds (company_id);
CREATE INDEX ix_virtual_stock_thresholds_set_at ON virtual_stock_thresholds (set_at);

CREATE TABLE price_review_cycle_settings (
    id INTEGER NOT NULL PRIMARY KEY,
    company_id INTEGER NOT NULL,
    review_cycle_days INTEGER NOT NULL,
    set_by INTEGER NOT NULL,
    set_at DATETIME NOT NULL
);

CREATE INDEX ix_price_review_cycle_settings_id ON price_review_cycle_settings (id);
CREATE INDEX ix_price_review_cycle_settings_company_id ON price_review_cycle_settings (company_id);
CREATE INDEX ix_price_review_cycle_settings_set_at ON price_review_cycle_settings (set_at);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로
-- 실행. 완전히 새로운 테이블이라 DROP TABLE로 데이터 손실 없이
-- 되돌릴 수 있다):
-- BEGIN;
-- DROP TABLE price_review_cycle_settings;
-- DROP TABLE virtual_stock_thresholds;
-- COMMIT;
