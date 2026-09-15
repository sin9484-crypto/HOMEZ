-- =========================================================
-- Homez OS
--
-- File : migrations/20260915_09_create_recall_notice_schema.sql
--
-- 2026-09-15 전면 감사 후속(Phase 9I/9J, HOMEZ_USER_OPERATION_SETTINGS.md
-- 10-17/10-18 — "리콜·판매중지 여부를 매일 확인하고, 확인된 문제
-- 상품은 신규 등록·가격 확대·가상재고 증가·자동발주를 동시에 막는다").
--
-- recall_notices: 확인된 리콜/판매중지 공고(전역, append-only,
-- dedupe_key 유니크). recall_check_runs: 매일 확인 Job 실행 기록
-- (성공/부분실패/실패). recall_check_job_states: Job 자동화 모드
-- append-only 설정(기본 PAUSED). recall_product_blocks: 회사별로
-- 격리된 "확인된 문제 상품" 차단 상태.
--
-- 기존 테이블·데이터는 전혀 건드리지 않는다.
-- =========================================================

BEGIN;

CREATE TABLE recall_notices (
	id INTEGER NOT NULL,
	product_identifier VARCHAR(200) NOT NULL,
	manufacturer VARCHAR(200),
	model VARCHAR(200),
	reason VARCHAR(500) NOT NULL,
	announcement_date DATETIME,
	source VARCHAR(100) NOT NULL,
	dedupe_key VARCHAR(300) NOT NULL,
	discovered_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_recall_notices_dedupe_key UNIQUE (dedupe_key)
);

CREATE INDEX ix_recall_notices_id ON recall_notices (id);
CREATE INDEX ix_recall_notices_product_identifier ON recall_notices (product_identifier);

CREATE TABLE recall_check_runs (
	id INTEGER NOT NULL,
	provider_name VARCHAR(100) NOT NULL,
	status VARCHAR(20) NOT NULL,
	notices_found_count INTEGER NOT NULL,
	new_notices_count INTEGER NOT NULL,
	error_detail VARCHAR(500),
	started_at DATETIME NOT NULL,
	finished_at DATETIME,
	PRIMARY KEY (id)
);

CREATE INDEX ix_recall_check_runs_id ON recall_check_runs (id);
CREATE INDEX ix_recall_check_runs_status ON recall_check_runs (status);

CREATE TABLE recall_check_job_states (
	id INTEGER NOT NULL,
	mode VARCHAR(20) NOT NULL,
	set_by INTEGER NOT NULL,
	set_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_recall_check_job_states_id ON recall_check_job_states (id);

CREATE TABLE recall_product_blocks (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	product_identifier VARCHAR(200) NOT NULL,
	recall_notice_id INTEGER,
	status VARCHAR(20) NOT NULL,
	reason VARCHAR(500) NOT NULL,
	blocked_at DATETIME NOT NULL,
	unblock_requested_by INTEGER,
	unblock_justification VARCHAR(500),
	unblock_approved_by INTEGER,
	unblock_approved_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(recall_notice_id) REFERENCES recall_notices (id)
);

CREATE INDEX ix_recall_product_blocks_id ON recall_product_blocks (id);
CREATE INDEX ix_recall_product_blocks_company_id ON recall_product_blocks (company_id);
CREATE INDEX ix_recall_product_blocks_product_identifier ON recall_product_blocks (product_identifier);
CREATE INDEX ix_recall_product_blocks_status ON recall_product_blocks (status);

COMMIT;

-- Rollback(수동, 실제 DB 적용 시 별도 승인 후에만 사용):
-- DROP TABLE IF EXISTS recall_product_blocks;
-- DROP TABLE IF EXISTS recall_check_job_states;
-- DROP TABLE IF EXISTS recall_check_runs;
-- DROP TABLE IF EXISTS recall_notices;
