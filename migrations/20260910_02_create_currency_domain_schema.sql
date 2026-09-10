-- Purpose: HOMEZ 사용자 운영 기준(HOMEZ_USER_OPERATION_SETTINGS.md)
-- 6번 — "초기 환율 변동 허용률: 2%, 추후 환율을 자동 모니터링하는
-- 기능을 개발한다." Phase 9 구현.
-- Source of Truth: app/domains/currency/model.py
--                   (ExchangeRate, ExchangeRateToleranceSetting)
--
-- 둘 다 append-only다 — (base_currency, quote_currency) 또는
-- company_id별 가장 최근 행이 현재 값.
--
-- 완전히 새로운 테이블만 추가한다(ALTER 없음) — 기존 테이블은 전혀
-- 건드리지 않는다.

BEGIN;

CREATE TABLE exchange_rates (
    id INTEGER NOT NULL PRIMARY KEY,
    base_currency VARCHAR(10) NOT NULL,
    quote_currency VARCHAR(10) NOT NULL,
    rate FLOAT NOT NULL,
    source VARCHAR(40) NOT NULL,
    recorded_by INTEGER,
    recorded_at DATETIME NOT NULL
);

CREATE INDEX ix_exchange_rates_id ON exchange_rates (id);
CREATE INDEX ix_exchange_rates_base_currency ON exchange_rates (base_currency);
CREATE INDEX ix_exchange_rates_quote_currency ON exchange_rates (quote_currency);
CREATE INDEX ix_exchange_rates_recorded_at ON exchange_rates (recorded_at);

CREATE TABLE exchange_rate_tolerance_settings (
    id INTEGER NOT NULL PRIMARY KEY,
    company_id INTEGER NOT NULL,
    tolerance_percent FLOAT NOT NULL,
    set_by INTEGER NOT NULL,
    set_at DATETIME NOT NULL
);

CREATE INDEX ix_exchange_rate_tolerance_settings_id ON exchange_rate_tolerance_settings (id);
CREATE INDEX ix_exchange_rate_tolerance_settings_company_id ON exchange_rate_tolerance_settings (company_id);
CREATE INDEX ix_exchange_rate_tolerance_settings_set_at ON exchange_rate_tolerance_settings (set_at);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로
-- 실행. 완전히 새로운 테이블이라 DROP TABLE로 데이터 손실 없이
-- 되돌릴 수 있다 — 단, 이미 실제 환율 기록·허용률 이력이 쌓였다면
-- 그 이력 자체는 DROP TABLE로 사라진다):
-- BEGIN;
-- DROP TABLE exchange_rate_tolerance_settings;
-- DROP TABLE exchange_rates;
-- COMMIT;
