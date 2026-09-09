-- Purpose: Gate R13(2026-08-14) — coupang/decision/product_candidate/
-- settlement 테넌트(회사) 격리 결함 수정.
-- Source of Truth: app/domains/coupang/model.py, app/domains/decision/
-- model.py, app/domains/product_candidate/model.py,
-- app/domains/settlement/model.py
--
-- 배경: 독립 재감사에서 coupang_*/decision_* 핵심 테이블에 company_id
-- 컬럼 자체가 없어, 어떤 회사의 관리자든 다른 회사의 쿠팡 판매 초안
-- (판매가·마진)과 Decision AI 평가(예상매출·가용자금)를 열람·승인·
-- 재정의할 수 있는 상태임을 확인했다. product_candidate_decisions는
-- "회사가 이 후보를 어떻게 판단했는가"가 전역 단일 필드
-- (product_candidates.status)에만 반영되어, 한 회사의 승인에 다른
-- 회사가 무임승차할 수 있었다. marketplace_settlements는 이미 회사
-- 격리(FundingAccount.company_id 조인)가 적용돼 있었으나 idempotency_
-- key UNIQUE가 여전히 전역이었다.
--
-- 실행 전 필수 확인(2026-08-14 실제 homez.db 읽기 전용 재확인 결과,
-- mode=ro + PRAGMA query_only=ON): coupang_marketplace_products/
-- coupang_product_options/coupang_policy_sets/coupang_policy_rules/
-- coupang_product_notices/coupang_profit_estimates/
-- coupang_dry_run_attempts/coupang_integration_decisions/
-- decision_policies/decision_evaluations/decision_scores/
-- decision_reviews/decision_audit_logs/product_candidates/
-- product_candidate_decisions/product_candidate_evidences/
-- funding_accounts/marketplace_settlements 전부 정확히 0행이었고,
-- companies=1, users=1이었다 — 기존 데이터의 소유 회사를 추측해야
--하는 상황 자체가 없다(backfill 로직 불필요). 이 스크립트는 위 표에
-- 열거된 테이블들이 대상 DB에서 계속 0행인 상태를 전제로 한다 — 만약
-- 실제 적용 시점에 행이 하나라도 있다면(이 스크립트 작성 이후 새로
-- 생겼다면) 이 스크립트를 그대로 실행하지 말고 별도의 데이터 보존형
-- Migration으로 다시 설계해야 한다(DROP TABLE로 재생성하는 부분은
-- 데이터를 보존하지 않는다).
--
-- 이 스크립트가 사용하는 두 가지 기법:
--   (A) ALTER TABLE ... ADD COLUMN company_id INTEGER NOT NULL
--       DEFAULT 0 — SQLite는 ALTER TABLE ADD COLUMN에 NOT NULL을
--       주려면 반드시 DEFAULT가 있어야 한다(빈 테이블이어도 이
--       문법 제약은 동일하게 적용된다). 실제로 backfill될 기존 행이
--       0건이므로 이 DEFAULT 0은 순수하게 DDL 문법을 만족시키기
--       위한 것이며, 이후 모든 실제 INSERT는 서비스 레이어가 항상
--       명시적으로 company_id를 지정한다(Model의 Python 레벨에는
--       default가 없다 — nullable=False만 있다). 이 컬럼에
--       DEFAULT가 남아있다는 사실 자체는 드리프트 테스트에서
--       컬럼명·NOT NULL 여부만 비교하고 DEFAULT 값은 비교하지
--       않는 방식으로 이미 이 저장소의 다른 ALTER 기반 Migration
--       (예: 20260805_00)에서도 확립된 패턴이다.
--   (B) UNIQUE 제약을 (idempotency_key) 단일에서
--       (company_id, idempotency_key) 복합으로 바꿔야 하는 테이블
--       (coupang_marketplace_products/coupang_dry_run_attempts/
--       coupang_integration_decisions/decision_evaluations/
--       decision_reviews/marketplace_settlements)은 SQLite가
--       테이블 레벨 UNIQUE 제약을 ALTER TABLE만으로 바꿀 수 없어
--       재생성이 필요하다 — "<이름>_new로 CREATE → 기존 테이블
--       DROP → RENAME"기법을 쓴다(단순 DROP 후 같은 이름으로 다시
--       CREATE하지 않는다). 이유: 이 저장소의 공식
--       MigrationRunner(app/database/migration_runner.py)의
--       apply_pending()이 파일 안의 모든 `CREATE TABLE <이름>`을
--       사전 충돌 검사 대상으로 삼는데, 같은 파일 안에서 DROP 직후
--       동일 이름으로 다시 CREATE하면 "이미 존재하는 이름"으로
--       오탐되어 Runner가 DuplicateApplicationError로 적용 자체를
--       거부한다(직접 sqlite3.executescript로 실행하면 문제없이
--       성공하지만, 공식 Runner 경로로는 막힌다는 것을 리허설 중
--       실제로 재현·확인했다). RENAME 문은 이 사전 검사 정규식이
--       보지 않으므로 "_new 임시 이름으로 생성 → 원본 DROP → RENAME"
--       순서를 쓰면 Runner와 완전히 호환된다. 위 "실행 전 필수 확인"
--       에서 이 테이블들이 0행임을 전제하므로 데이터 손실이 없다
--       (SQLite는 DROP TABLE 시 그 테이블에 속한 인덱스도 함께
--       제거하므로 별도 DROP INDEX가 필요 없다).
--
-- CoupangPolicySet/CoupangPolicyRule/DecisionPolicy는 이번 Migration
-- 대상이 아니다(전역 플랫폼 설정으로 유지 — Model.py의 판단 근거
-- 참고). ProductCandidate/ProductCandidateEvidence 자체도 대상이
-- 아니다(전역 발견 카탈로그 유지).
--
-- 이 Migration은 실제 homez.db에 적용하지 않는다. 임시 DB(15개 기존
-- Migration을 순서대로 적용해 재현한 스키마)에서만 검증했다(
-- tests/test_gate_r13_tenant_isolation_migration.py).

BEGIN;

-- ====================================================
-- 1) coupang_marketplace_products — company_id + 복합 UNIQUE
-- ====================================================
--
-- SQLite는 테이블 레벨 UNIQUE 제약을 ALTER TABLE만으로 바꿀 수 없다.
-- 여기서는 "새 이름으로 CREATE → 기존 테이블 DROP → RENAME"
-- 기법을 쓴다(단순 DROP 후 같은 이름으로 CREATE하지 않는다) — 이
-- 저장소의 MigrationRunner.apply_pending()이 파일 안의 모든
-- `CREATE TABLE <이름>`을 사전 충돌 검사 대상으로 삼기 때문에,
-- 같은 파일 안에서 DROP 직후 동일 이름으로 다시 CREATE하면 "이미
-- 존재하는 이름"으로 오탐되어 적용 자체가 거부된다(RENAME 문은 이
-- 사전 검사가 보지 않는다). 0행 전제이므로 데이터 복사는 생략한다.

CREATE TABLE coupang_marketplace_products_new (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	marketplace VARCHAR(30) NOT NULL,
	sales_method VARCHAR(30) NOT NULL,
	product_candidate_id INTEGER NOT NULL,
	seller_product_id VARCHAR(100),
	vendor_item_id VARCHAR(100),
	external_vendor_sku VARCHAR(100) NOT NULL,
	display_category_code VARCHAR(50),
	seller_product_name VARCHAR(300) NOT NULL,
	brand VARCHAR(100),
	gtin VARCHAR(50),
	mpn VARCHAR(50),
	identifier_exemption_reason VARCHAR(500),
	sale_price NUMERIC(14, 2),
	supplier_stock INTEGER,
	safety_stock INTEGER NOT NULL,
	marketplace_exposure_stock INTEGER NOT NULL,
	last_stock_checked_at DATETIME,
	stock_review_required BOOLEAN NOT NULL,
	available_stock INTEGER NOT NULL,
	maximum_buy_count INTEGER,
	shipping_method VARCHAR(50),
	shipping_company_code VARCHAR(50),
	outbound_shipping_place_code VARCHAR(50),
	return_center_code VARCHAR(50),
	return_charge NUMERIC(14, 2),
	overseas_purchase_agency BOOLEAN NOT NULL,
	pcc_needed BOOLEAN NOT NULL,
	status VARCHAR(30) NOT NULL,
	validation_status VARCHAR(20) NOT NULL,
	validation_errors TEXT,
	risk_level VARCHAR(30),
	policy_version_applied VARCHAR(50),
	idempotency_key VARCHAR(120) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_coupang_products_idempotency UNIQUE (company_id, idempotency_key)
);

DROP TABLE coupang_marketplace_products;

ALTER TABLE coupang_marketplace_products_new RENAME TO coupang_marketplace_products;

CREATE INDEX ix_coupang_marketplace_products_company_id ON coupang_marketplace_products (company_id);
CREATE INDEX ix_coupang_marketplace_products_external_vendor_sku ON coupang_marketplace_products (external_vendor_sku);
CREATE INDEX ix_coupang_marketplace_products_id ON coupang_marketplace_products (id);
CREATE INDEX ix_coupang_marketplace_products_product_candidate_id ON coupang_marketplace_products (product_candidate_id);
CREATE INDEX ix_coupang_marketplace_products_status ON coupang_marketplace_products (status);

-- ====================================================
-- 2) coupang_product_options — company_id (단순 ADD, UNIQUE 없음)
-- ====================================================

ALTER TABLE coupang_product_options ADD COLUMN company_id INTEGER NOT NULL DEFAULT 0;
CREATE INDEX ix_coupang_product_options_company_id ON coupang_product_options (company_id);

-- ====================================================
-- 3) coupang_product_notices — company_id
-- ====================================================

ALTER TABLE coupang_product_notices ADD COLUMN company_id INTEGER NOT NULL DEFAULT 0;
CREATE INDEX ix_coupang_product_notices_company_id ON coupang_product_notices (company_id);

-- ====================================================
-- 4) coupang_profit_estimates — company_id
-- ====================================================

ALTER TABLE coupang_profit_estimates ADD COLUMN company_id INTEGER NOT NULL DEFAULT 0;
CREATE INDEX ix_coupang_profit_estimates_company_id ON coupang_profit_estimates (company_id);

-- ====================================================
-- 5) coupang_dry_run_attempts — company_id + 복합 UNIQUE
-- ====================================================

CREATE TABLE coupang_dry_run_attempts_new (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	coupang_product_id INTEGER NOT NULL,
	idempotency_key VARCHAR(120) NOT NULL,
	outcome VARCHAR(20) NOT NULL,
	errors TEXT,
	payload_field_count INTEGER NOT NULL,
	attempted_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_coupang_dry_run_idempotency UNIQUE (company_id, idempotency_key)
);

DROP TABLE coupang_dry_run_attempts;

ALTER TABLE coupang_dry_run_attempts_new RENAME TO coupang_dry_run_attempts;

CREATE INDEX ix_coupang_dry_run_attempts_company_id ON coupang_dry_run_attempts (company_id);
CREATE INDEX ix_coupang_dry_run_attempts_coupang_product_id ON coupang_dry_run_attempts (coupang_product_id);
CREATE INDEX ix_coupang_dry_run_attempts_id ON coupang_dry_run_attempts (id);

-- ====================================================
-- 6) coupang_integration_decisions — company_id + 복합 UNIQUE
-- ====================================================

CREATE TABLE coupang_integration_decisions_new (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	coupang_product_id INTEGER NOT NULL,
	action VARCHAR(40) NOT NULL,
	operator_id INTEGER NOT NULL,
	idempotency_key VARCHAR(120) NOT NULL,
	memo VARCHAR(1000),
	decided_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_coupang_decisions_idempotency UNIQUE (company_id, idempotency_key)
);

DROP TABLE coupang_integration_decisions;

ALTER TABLE coupang_integration_decisions_new RENAME TO coupang_integration_decisions;

CREATE INDEX ix_coupang_integration_decisions_company_id ON coupang_integration_decisions (company_id);
CREATE INDEX ix_coupang_integration_decisions_coupang_product_id ON coupang_integration_decisions (coupang_product_id);
CREATE INDEX ix_coupang_integration_decisions_id ON coupang_integration_decisions (id);

-- ====================================================
-- 7) decision_evaluations — company_id + 복합 UNIQUE
-- ====================================================

CREATE TABLE decision_evaluations_new (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	candidate_id INTEGER NOT NULL,
	policy_id INTEGER NOT NULL,
	policy_version VARCHAR(50) NOT NULL,
	input_snapshot_json TEXT NOT NULL,
	input_fingerprint VARCHAR(64) NOT NULL,
	total_score NUMERIC(9, 4) NOT NULL,
	confidence NUMERIC(9, 4) NOT NULL,
	recommendation VARCHAR(30) NOT NULL,
	recommendation_reason VARCHAR(1000) NOT NULL,
	blocked_by_safety BOOLEAN NOT NULL,
	safety_block_reason VARCHAR(500),
	status VARCHAR(20) NOT NULL,
	idempotency_key VARCHAR(160) NOT NULL,
	created_by VARCHAR(50) NOT NULL,
	evaluator_kind VARCHAR(50) NOT NULL,
	evaluator_version VARCHAR(50) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_decision_evaluations_idempotency UNIQUE (company_id, idempotency_key)
);

DROP TABLE decision_evaluations;

ALTER TABLE decision_evaluations_new RENAME TO decision_evaluations;

CREATE INDEX ix_decision_evaluations_candidate_id ON decision_evaluations (candidate_id);
CREATE INDEX ix_decision_evaluations_company_id ON decision_evaluations (company_id);
CREATE INDEX ix_decision_evaluations_id ON decision_evaluations (id);
CREATE INDEX ix_decision_evaluations_input_fingerprint ON decision_evaluations (input_fingerprint);
CREATE INDEX ix_decision_evaluations_policy_id ON decision_evaluations (policy_id);
CREATE INDEX ix_decision_evaluations_recommendation ON decision_evaluations (recommendation);
CREATE INDEX ix_decision_evaluations_status ON decision_evaluations (status);

-- ====================================================
-- 8) decision_scores — company_id
-- ====================================================

ALTER TABLE decision_scores ADD COLUMN company_id INTEGER NOT NULL DEFAULT 0;
CREATE INDEX ix_decision_scores_company_id ON decision_scores (company_id);

-- ====================================================
-- 9) decision_reviews — company_id + 복합 UNIQUE
-- ====================================================

CREATE TABLE decision_reviews_new (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	evaluation_id INTEGER NOT NULL,
	action VARCHAR(20) NOT NULL,
	reviewer_id INTEGER NOT NULL,
	memo VARCHAR(1000),
	override_reason VARCHAR(1000),
	previous_value VARCHAR(200),
	new_value VARCHAR(200),
	idempotency_key VARCHAR(160) NOT NULL,
	decided_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_decision_reviews_idempotency UNIQUE (company_id, idempotency_key)
);

DROP TABLE decision_reviews;

ALTER TABLE decision_reviews_new RENAME TO decision_reviews;

CREATE INDEX ix_decision_reviews_company_id ON decision_reviews (company_id);
CREATE INDEX ix_decision_reviews_evaluation_id ON decision_reviews (evaluation_id);
CREATE INDEX ix_decision_reviews_id ON decision_reviews (id);

-- ====================================================
-- 10) decision_audit_logs — company_id
-- ====================================================

ALTER TABLE decision_audit_logs ADD COLUMN company_id INTEGER NOT NULL DEFAULT 0;
CREATE INDEX ix_decision_audit_logs_company_id ON decision_audit_logs (company_id);

-- ====================================================
-- 11) product_candidate_decisions — company_id (단순 ADD, UNIQUE 없음)
-- ====================================================

ALTER TABLE product_candidate_decisions ADD COLUMN company_id INTEGER NOT NULL DEFAULT 0;
CREATE INDEX ix_product_candidate_decisions_company_id ON product_candidate_decisions (company_id);

-- ====================================================
-- 12) product_candidate_selections — 신규 테이블(회사별 현재 승인 상태 투영)
-- ====================================================

CREATE TABLE product_candidate_selections (
	id INTEGER NOT NULL,
	candidate_id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	status VARCHAR(20) NOT NULL,
	decided_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_product_candidate_selections_candidate_company UNIQUE (candidate_id, company_id)
);

CREATE INDEX ix_product_candidate_selections_candidate_id ON product_candidate_selections (candidate_id);
CREATE INDEX ix_product_candidate_selections_company_id ON product_candidate_selections (company_id);
CREATE INDEX ix_product_candidate_selections_id ON product_candidate_selections (id);

-- ====================================================
-- 13) marketplace_settlements — company_id + 복합 UNIQUE
-- ====================================================

CREATE TABLE marketplace_settlements_new (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
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
	CONSTRAINT uq_marketplace_settlements_idempotency UNIQUE (company_id, idempotency_key)
);

DROP TABLE marketplace_settlements;

ALTER TABLE marketplace_settlements_new RENAME TO marketplace_settlements;

CREATE INDEX ix_marketplace_settlements_company_id ON marketplace_settlements (company_id);
CREATE INDEX ix_marketplace_settlements_market ON marketplace_settlements (market);
CREATE INDEX ix_marketplace_settlements_account_id ON marketplace_settlements (account_id);
CREATE INDEX ix_marketplace_settlements_market_order_id ON marketplace_settlements (market_order_id);
CREATE INDEX ix_marketplace_settlements_status ON marketplace_settlements (status);
CREATE INDEX ix_marketplace_settlements_id ON marketplace_settlements (id);
CREATE INDEX ix_marketplace_settlements_order_id ON marketplace_settlements (order_id);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로
-- 실행, 단 이 rollback은 company_id 도입 이전 원본 스키마로 완전히
-- 되돌린다는 뜻이며 그 사이 생성된 company_id 데이터는 버려진다는
-- 점을 반드시 인지해야 한다. 마찬가지로 DROP TABLE 방식이므로
-- rollback 시점에 각 테이블이 실제로 비어 있는지 먼저 확인하라):
-- BEGIN;
-- DROP TABLE marketplace_settlements;
-- CREATE TABLE marketplace_settlements ( id INTEGER NOT NULL, market VARCHAR(30) NOT NULL, market_order_id VARCHAR(100) NOT NULL, order_id INTEGER, account_id INTEGER NOT NULL, gross_amount FLOAT NOT NULL, fee_amount FLOAT NOT NULL, net_amount FLOAT NOT NULL, status VARCHAR(20) NOT NULL, deposited_at DATETIME, funding_ledger_id INTEGER, idempotency_key VARCHAR(120) NOT NULL, memo VARCHAR(500), created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id), CONSTRAINT uq_marketplace_settlements_idempotency UNIQUE (idempotency_key) );
-- CREATE INDEX ix_marketplace_settlements_market ON marketplace_settlements (market);
-- CREATE INDEX ix_marketplace_settlements_account_id ON marketplace_settlements (account_id);
-- CREATE INDEX ix_marketplace_settlements_market_order_id ON marketplace_settlements (market_order_id);
-- CREATE INDEX ix_marketplace_settlements_status ON marketplace_settlements (status);
-- CREATE INDEX ix_marketplace_settlements_id ON marketplace_settlements (id);
-- CREATE INDEX ix_marketplace_settlements_order_id ON marketplace_settlements (order_id);
-- DROP TABLE product_candidate_selections;
-- DROP INDEX ix_product_candidate_decisions_company_id;
-- ALTER TABLE product_candidate_decisions DROP COLUMN company_id;
-- DROP INDEX ix_decision_audit_logs_company_id;
-- ALTER TABLE decision_audit_logs DROP COLUMN company_id;
-- DROP TABLE decision_reviews;
-- CREATE TABLE decision_reviews ( id INTEGER NOT NULL, evaluation_id INTEGER NOT NULL, action VARCHAR(20) NOT NULL, reviewer_id INTEGER NOT NULL, memo VARCHAR(1000), override_reason VARCHAR(1000), previous_value VARCHAR(200), new_value VARCHAR(200), idempotency_key VARCHAR(160) NOT NULL, decided_at DATETIME NOT NULL, PRIMARY KEY (id), CONSTRAINT uq_decision_reviews_idempotency UNIQUE (idempotency_key) );
-- CREATE INDEX ix_decision_reviews_evaluation_id ON decision_reviews (evaluation_id);
-- CREATE INDEX ix_decision_reviews_id ON decision_reviews (id);
-- DROP INDEX ix_decision_scores_company_id;
-- ALTER TABLE decision_scores DROP COLUMN company_id;
-- DROP TABLE decision_evaluations;
-- CREATE TABLE decision_evaluations ( id INTEGER NOT NULL, candidate_id INTEGER NOT NULL, policy_id INTEGER NOT NULL, policy_version VARCHAR(50) NOT NULL, input_snapshot_json TEXT NOT NULL, input_fingerprint VARCHAR(64) NOT NULL, total_score NUMERIC(9, 4) NOT NULL, confidence NUMERIC(9, 4) NOT NULL, recommendation VARCHAR(30) NOT NULL, recommendation_reason VARCHAR(1000) NOT NULL, blocked_by_safety BOOLEAN NOT NULL, safety_block_reason VARCHAR(500), status VARCHAR(20) NOT NULL, idempotency_key VARCHAR(160) NOT NULL, created_by VARCHAR(50) NOT NULL, evaluator_kind VARCHAR(50) NOT NULL, evaluator_version VARCHAR(50) NOT NULL, created_at DATETIME NOT NULL, PRIMARY KEY (id), CONSTRAINT uq_decision_evaluations_idempotency UNIQUE (idempotency_key) );
-- CREATE INDEX ix_decision_evaluations_candidate_id ON decision_evaluations (candidate_id);
-- CREATE INDEX ix_decision_evaluations_id ON decision_evaluations (id);
-- CREATE INDEX ix_decision_evaluations_input_fingerprint ON decision_evaluations (input_fingerprint);
-- CREATE INDEX ix_decision_evaluations_policy_id ON decision_evaluations (policy_id);
-- CREATE INDEX ix_decision_evaluations_recommendation ON decision_evaluations (recommendation);
-- CREATE INDEX ix_decision_evaluations_status ON decision_evaluations (status);
-- DROP TABLE coupang_integration_decisions;
-- CREATE TABLE coupang_integration_decisions ( id INTEGER NOT NULL, coupang_product_id INTEGER NOT NULL, action VARCHAR(40) NOT NULL, operator_id INTEGER NOT NULL, idempotency_key VARCHAR(120) NOT NULL, memo VARCHAR(1000), decided_at DATETIME NOT NULL, PRIMARY KEY (id), CONSTRAINT uq_coupang_decisions_idempotency UNIQUE (idempotency_key) );
-- CREATE INDEX ix_coupang_integration_decisions_coupang_product_id ON coupang_integration_decisions (coupang_product_id);
-- CREATE INDEX ix_coupang_integration_decisions_id ON coupang_integration_decisions (id);
-- DROP TABLE coupang_dry_run_attempts;
-- CREATE TABLE coupang_dry_run_attempts ( id INTEGER NOT NULL, coupang_product_id INTEGER NOT NULL, idempotency_key VARCHAR(120) NOT NULL, outcome VARCHAR(20) NOT NULL, errors TEXT, payload_field_count INTEGER NOT NULL, attempted_at DATETIME NOT NULL, PRIMARY KEY (id), CONSTRAINT uq_coupang_dry_run_idempotency UNIQUE (idempotency_key) );
-- CREATE INDEX ix_coupang_dry_run_attempts_coupang_product_id ON coupang_dry_run_attempts (coupang_product_id);
-- CREATE INDEX ix_coupang_dry_run_attempts_id ON coupang_dry_run_attempts (id);
-- DROP INDEX ix_coupang_profit_estimates_company_id;
-- ALTER TABLE coupang_profit_estimates DROP COLUMN company_id;
-- DROP INDEX ix_coupang_product_notices_company_id;
-- ALTER TABLE coupang_product_notices DROP COLUMN company_id;
-- DROP INDEX ix_coupang_product_options_company_id;
-- ALTER TABLE coupang_product_options DROP COLUMN company_id;
-- DROP TABLE coupang_marketplace_products;
-- CREATE TABLE coupang_marketplace_products ( id INTEGER NOT NULL, marketplace VARCHAR(30) NOT NULL, sales_method VARCHAR(30) NOT NULL, product_candidate_id INTEGER NOT NULL, seller_product_id VARCHAR(100), vendor_item_id VARCHAR(100), external_vendor_sku VARCHAR(100) NOT NULL, display_category_code VARCHAR(50), seller_product_name VARCHAR(300) NOT NULL, brand VARCHAR(100), gtin VARCHAR(50), mpn VARCHAR(50), identifier_exemption_reason VARCHAR(500), sale_price NUMERIC(14, 2), supplier_stock INTEGER, safety_stock INTEGER NOT NULL, marketplace_exposure_stock INTEGER NOT NULL, last_stock_checked_at DATETIME, stock_review_required BOOLEAN NOT NULL, available_stock INTEGER NOT NULL, maximum_buy_count INTEGER, shipping_method VARCHAR(50), shipping_company_code VARCHAR(50), outbound_shipping_place_code VARCHAR(50), return_center_code VARCHAR(50), return_charge NUMERIC(14, 2), overseas_purchase_agency BOOLEAN NOT NULL, pcc_needed BOOLEAN NOT NULL, status VARCHAR(30) NOT NULL, validation_status VARCHAR(20) NOT NULL, validation_errors TEXT, risk_level VARCHAR(30), policy_version_applied VARCHAR(50), idempotency_key VARCHAR(120) NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id), CONSTRAINT uq_coupang_products_idempotency UNIQUE (idempotency_key) );
-- CREATE INDEX ix_coupang_marketplace_products_external_vendor_sku ON coupang_marketplace_products (external_vendor_sku);
-- CREATE INDEX ix_coupang_marketplace_products_id ON coupang_marketplace_products (id);
-- CREATE INDEX ix_coupang_marketplace_products_product_candidate_id ON coupang_marketplace_products (product_candidate_id);
-- CREATE INDEX ix_coupang_marketplace_products_status ON coupang_marketplace_products (status);
-- COMMIT;
