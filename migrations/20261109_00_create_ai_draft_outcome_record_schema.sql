-- =========================================================
-- Homez OS
--
-- File : migrations/20261109_00_create_ai_draft_outcome_record_schema.sql
--
-- 2026-11-09 AI 초안 평가 고리 — AI 초안과 사람이 실제로 쓴 글을 함께 남기는 신규 테이블 1개(추가형).
--   ai_draft_outcome_records : 초안 1건(기능·참조·모델·프롬프트 지문·초안 글·보낸 글)
-- Model(app/domains/ai_learning/model.py::AiDraftOutcomeRecord)의 SQLite DDL 그대로다(시험이 대조한다).
-- 기존 테이블·데이터는 전혀 건드리지 않는다. 비밀값 없음. 글은 개인정보를 가린 뒤 저장하고, 고객 문의 원문은 저장하지 않는다.
-- 실제 DB 적용은 앱의 Migration 승인 화면에서 사용자가 승인할 때만(백업 후) 일어난다.
-- =========================================================

BEGIN;

CREATE TABLE ai_draft_outcome_records (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	feature VARCHAR(20) NOT NULL,
	ref VARCHAR(40) NOT NULL,
	model VARCHAR(80),
	prompt_version VARCHAR(40) NOT NULL,
	draft_json TEXT NOT NULL,
	final_json TEXT,
	final_source VARCHAR(20),
	final_ref VARCHAR(60),
	created_by INTEGER,
	created_at DATETIME NOT NULL,
	finalized_at DATETIME,
	PRIMARY KEY (id)
);

CREATE INDEX ix_ai_draft_outcome_records_company_id ON ai_draft_outcome_records (company_id);

CREATE INDEX ix_ai_draft_outcome_records_id ON ai_draft_outcome_records (id);

COMMIT;

-- rollback(자동 실행되지 않음):
-- DROP INDEX IF EXISTS ix_ai_draft_outcome_records_id;
-- DROP INDEX IF EXISTS ix_ai_draft_outcome_records_company_id;
-- DROP TABLE IF EXISTS ai_draft_outcome_records;
