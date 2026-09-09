-- Purpose: 채널 정책 엔진(2026-08-21 CP-2, CTO 지시) — 판매채널
-- 선택 시 쿠팡·네이버 정책이 자동 적용되는 정책 엔진의 스키마.
-- Source of Truth: app/domains/channel_policy/model.py
--
-- 3개 신규 테이블:
--   channel_policy_rules: 실제 공식 채널 정책·API 문서로 확인된
--     규칙 카탈로그(전역 공유, company_id 없음 — Marketplace
--     FulfillmentCapability와 동일한 원칙).
--   company_channel_policy_settings: 회사별 목표 마진율·구매한도
--     등(정책 적합성과 완전히 분리된 축, company_id당 1행).
--   channel_policy_evaluations: 평가 이력(append-only, 정책 스냅샷).
--
-- 2026-08-21 CA-1(제출 직전 정책 강제 게이트) 갱신 — channel_policy_
-- evaluations에 input_fingerprint 컬럼 추가. 이 파일은 실제 DB에
-- 지금까지 한 번도 적용된 적이 없으므로(diagnose() pending 확인됨)
-- 새 Migration을 만들지 않고 이 자리에서 직접 갱신한다 — 이미 적용된
-- 파일의 checksum 불변 원칙과 충돌하지 않는다.
--
-- 이 Migration은 실제 homez.db(개발·운영 모두)에 적용하지 않는다.
-- 임시 SQLite 파일에서만 검증했다(tests/test_channel_policy_engine.py).

BEGIN;

CREATE TABLE channel_policy_rules (
	id INTEGER NOT NULL,
	rule_code VARCHAR(80) NOT NULL,
	channel VARCHAR(30) NOT NULL,
	category_scope_json TEXT NOT NULL,
	severity VARCHAR(20) NOT NULL,
	validation_type VARCHAR(40) NOT NULL,
	required_fields_json TEXT,
	required_evidence_json TEXT,
	official_source_url VARCHAR(500),
	source_title VARCHAR(300),
	verified_at DATETIME,
	effective_from DATETIME,
	profile_version VARCHAR(50) NOT NULL,
	active BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_channel_policy_rules_channel_rule_code UNIQUE (channel, rule_code)
);

CREATE INDEX ix_channel_policy_rules_active ON channel_policy_rules (active);
CREATE INDEX ix_channel_policy_rules_id ON channel_policy_rules (id);
CREATE INDEX ix_channel_policy_rules_channel ON channel_policy_rules (channel);

CREATE TABLE company_channel_policy_settings (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	min_target_margin_rate NUMERIC(6, 4),
	min_profit_per_order NUMERIC(14, 2),
	max_initial_purchase_amount NUMERIC(14, 2),
	max_moq INTEGER,
	max_lead_time_days INTEGER,
	max_return_shipping_cost NUMERIC(14, 2),
	allowed_categories_json TEXT NOT NULL,
	forbidden_categories_json TEXT NOT NULL,
	safety_stock_buffer INTEGER,
	updated_by INTEGER NOT NULL,
	version INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_company_channel_policy_settings_company UNIQUE (company_id)
);

CREATE INDEX ix_company_channel_policy_settings_company_id ON company_channel_policy_settings (company_id);
CREATE INDEX ix_company_channel_policy_settings_id ON company_channel_policy_settings (id);

CREATE TABLE channel_policy_evaluations (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	product_candidate_id INTEGER NOT NULL,
	channel VARCHAR(30) NOT NULL,
	result VARCHAR(40) NOT NULL,
	policy_profile_version VARCHAR(50) NOT NULL,
	rule_results_json TEXT NOT NULL,
	input_fingerprint VARCHAR(64) NOT NULL,
	evaluated_by INTEGER,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_channel_policy_evaluations_result ON channel_policy_evaluations (result);
CREATE INDEX ix_channel_policy_evaluations_company_id ON channel_policy_evaluations (company_id);
CREATE INDEX ix_channel_policy_evaluations_created_at ON channel_policy_evaluations (created_at);
CREATE INDEX ix_channel_policy_evaluations_channel ON channel_policy_evaluations (channel);
CREATE INDEX ix_channel_policy_evaluations_product_candidate_id ON channel_policy_evaluations (product_candidate_id);
CREATE INDEX ix_channel_policy_evaluations_id ON channel_policy_evaluations (id);

COMMIT;

-- Rollback(주석 전용, 자동 실행되지 않음):
-- DROP TABLE channel_policy_evaluations;
-- DROP TABLE company_channel_policy_settings;
-- DROP TABLE channel_policy_rules;
