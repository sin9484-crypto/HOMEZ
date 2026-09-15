-- =========================================================
-- Homez OS
--
-- File : migrations/20260915_05_add_supplier_incident_and_connection_health_fields.sql
--
-- 2026-09-15 전면 감사 후속(Phase 9B/9C, HOMEZ_USER_OPERATION_
-- SETTINGS.md 7-11/7-16).
--
-- purchase_channel_connections에 3개 컬럼을 추가한다:
--   - rate_limited_until(Phase 9A, 7-8): 429 응답 후 재시도 가능
--     시각.
--   - order_paused_at / order_paused_reason(Phase 9B, 7-11): 반복
--     사건(품절/오배송/취소/배송지연)으로 이 연결의 발주 기능만
--     일시중지된 시각·사유.
--   - last_successful_order_at(Phase 9C, 7-16): 마지막 발주 성공
--     시각 — 휴면 판정 기준.
--
-- 신규 테이블 2개를 추가한다:
--   - purchase_channel_connection_incidents(Phase 9B): 품절/오배송/
--     취소/배송지연/인증실패 append-only 사건 기록.
--   - supplier_incident_auto_pause_settings(Phase 9B): 회사별
--     자동일시중지 기준(기간·횟수) append-only 설정.
--
-- 기존 컬럼·데이터는 전혀 건드리지 않는다. 새 컬럼은 전부 nullable
-- 이므로 기존 연결 행은 NULL(=값 없음, 사실과 일치 — 이 컬럼들이
-- 추적하는 사건은 이 Migration 이전에는 기록된 적이 없었다)로
-- 채워진다.
-- =========================================================

BEGIN;

ALTER TABLE purchase_channel_connections ADD COLUMN rate_limited_until DATETIME;
ALTER TABLE purchase_channel_connections ADD COLUMN order_paused_at DATETIME;
ALTER TABLE purchase_channel_connections ADD COLUMN order_paused_reason VARCHAR(500);
ALTER TABLE purchase_channel_connections ADD COLUMN last_successful_order_at DATETIME;

CREATE TABLE purchase_channel_connection_incidents (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	connection_id INTEGER NOT NULL,
	incident_type VARCHAR(30) NOT NULL,
	detail VARCHAR(500),
	recorded_by INTEGER,
	occurred_at DATETIME NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_purchase_channel_connection_incidents_id
ON purchase_channel_connection_incidents (id);
CREATE INDEX ix_purchase_channel_connection_incidents_company_id
ON purchase_channel_connection_incidents (company_id);
CREATE INDEX ix_purchase_channel_connection_incidents_connection_id
ON purchase_channel_connection_incidents (connection_id);
CREATE INDEX ix_purchase_channel_connection_incidents_occurred_at
ON purchase_channel_connection_incidents (occurred_at);

CREATE TABLE supplier_incident_auto_pause_settings (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	window_days INTEGER NOT NULL,
	max_incident_count INTEGER NOT NULL,
	set_by INTEGER NOT NULL,
	set_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_supplier_incident_auto_pause_settings_id
ON supplier_incident_auto_pause_settings (id);
CREATE INDEX ix_supplier_incident_auto_pause_settings_company_id
ON supplier_incident_auto_pause_settings (company_id);
CREATE INDEX ix_supplier_incident_auto_pause_settings_set_at
ON supplier_incident_auto_pause_settings (set_at);

COMMIT;

-- Rollback(수동, 실제 DB 적용 시 별도 승인 후에만 사용):
-- DROP TABLE IF EXISTS supplier_incident_auto_pause_settings;
-- DROP TABLE IF EXISTS purchase_channel_connection_incidents;
-- SQLite는 컬럼 DROP을 직접 지원하지 않는다 — purchase_channel_
-- connections의 4개 컬럼을 되돌리려면 테이블을 재생성해야 한다.
