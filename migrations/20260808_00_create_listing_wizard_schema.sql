-- Purpose: Gate I(2026-08-08) — 상품등록 통합 마법사.
-- Source of Truth: app/domains/marketplace_listing/model.py::ListingWizard
--
-- 신규 테이블 1개만 추가한다(순수 CREATE TABLE + CREATE INDEX, 기존
-- 어떤 테이블·컬럼도 건드리지 않음). docs/V6_EXECUTION_LEDGER.md
-- "Gate I 설계" 3-1항 참고 — 이 테이블은 기존 marketplace_listing_
-- drafts(= `ml*` Console 마법사 전용)를 대체하지 않는다. 둘은 서로
-- 다른 목적의 별개 테이블로 공존한다.
--
-- DDL은 SQLAlchemy CreateTable/CreateIndex를 sqlite dialect로 컴파일한
-- 결과를 그대로 옮겼다(migration-safety 컨벤션) — Model에 없는
-- CHECK/DEFAULT/CASCADE/trigger/FK를 추가하지 않았다.
--
-- 이 Migration은 실제 homez.db에 적용하지 않는다. 임시 DB 리허설로만
-- 검증했다(LIVE_GATE_QUEUE 대기).

BEGIN;

CREATE TABLE listing_wizards (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	created_by_user_id INTEGER NOT NULL,
	current_step VARCHAR(30) NOT NULL,
	status VARCHAR(30) NOT NULL,
	source_type VARCHAR(20) NOT NULL,
	product_candidate_id INTEGER,
	cloned_from_wizard_id INTEGER,
	draft_json TEXT,
	selected_media_asset_ids_json TEXT NOT NULL,
	channel_selections_json TEXT NOT NULL,
	economics_input_json TEXT,
	economics_result_json TEXT,
	validation_result_json TEXT,
	approval_package_json TEXT,
	approval_fingerprint VARCHAR(64),
	approved_by_user_id INTEGER,
	approved_at DATETIME,
	approval_history_json TEXT NOT NULL,
	materialized_listing_ids_json TEXT NOT NULL,
	autosave_client_token VARCHAR(100),
	autosave_saved_at DATETIME,
	version INTEGER NOT NULL,
	creation_idempotency_key VARCHAR(160) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_listing_wizards_company_creation_idempotency UNIQUE (company_id, creation_idempotency_key)
);

CREATE INDEX ix_listing_wizards_id ON listing_wizards (id);
CREATE INDEX ix_listing_wizards_status ON listing_wizards (status);
CREATE INDEX ix_listing_wizards_company_id ON listing_wizards (company_id);
CREATE INDEX ix_listing_wizards_product_candidate_id ON listing_wizards (product_candidate_id);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로 실행):
-- BEGIN;
-- DROP INDEX ix_listing_wizards_product_candidate_id;
-- DROP INDEX ix_listing_wizards_company_id;
-- DROP INDEX ix_listing_wizards_status;
-- DROP INDEX ix_listing_wizards_id;
-- DROP TABLE listing_wizards;
-- COMMIT;
