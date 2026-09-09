-- Purpose: V7 Gate 2(2026-08-15) — 테넌트 격리 하드닝.
-- Source of Truth: app/domains/decision/model.py,
-- app/domains/product_candidate/model.py, app/domains/funding/model.py
--
-- 배경(CTO 지시 원문 요구사항 1/2/3):
-- 1) DecisionPolicy 전역/회사 분리 — 전역 시스템 기본 템플릿
--    (decision_policies, 기존 유지)과 회사별 적용 정책(신규
--    company_decision_policies)으로 나눈다. decision_evaluations가
--    어느 정책 버전을 근거로 평가됐는지(policy_source +
--    policy_id/company_policy_id) 감사 가능하게 만든다.
-- 2) product_candidate 재구조화 — 원본 발견 카탈로그는 전역 공유로
--    유지하되, 회사가 비공개(PRIVATE) 후보를 등록·취급할 수 있는
--    경로를 신설한다(product_candidates.visibility/owner_company_id).
--    ProductCandidateSelection에 최신 메모 투영(memo)을 추가한다.
-- 3) Funding 회사 스코프 강화 — FundingAccount.company_id를
--    nullable→NOT NULL로 전환(기본 계정 선택이 항상 company_id를
--    요구하도록 스키마 레벨에서 강제)하고, FundingHold/
--    SupplierPayment에 company_id를 추가해 idempotency_key UNIQUE를
--    (company_id, idempotency_key) 복합으로 바꾼다(coupang/decision/
--    settlement와 동일한 방어 수준). FundingLedger에도 company_id를
--    추가한다(회사별 조회용, UNIQUE 범위는 변경하지 않음 — 상세
--    사유는 app/domains/funding/model.py 상단 주석 참고).
--
-- 실행 전 필수 확인(2026-08-15 실제 homez.db 읽기 전용 재확인 결과,
-- mode=ro + PRAGMA query_only=ON): decision_policies/
-- decision_evaluations/product_candidates/product_candidate_selections/
-- funding_accounts/funding_ledgers/funding_holds/supplier_payments
-- 전부 정확히 0행이었고, companies=1이었다 — 기존 데이터의 소유 회사를
-- 추측해야 하는 상황 자체가 없다(backfill 로직 불필요). 이 스크립트는
-- 위 표에 열거된 테이블들이 대상 DB에서 계속 0행인 상태를 전제로
-- 한다 — 만약 실제 적용 시점에 행이 하나라도 있다면(이 스크립트 작성
-- 이후 새로 생겼다면) 이 스크립트를 그대로 실행하지 말고 별도의 데이터
-- 보존형 Migration으로 다시 설계해야 한다(DROP TABLE로 재생성하는
-- 부분은 데이터를 보존하지 않는다).
--
-- 이 스크립트가 사용하는 두 가지 기법(Gate R13과 동일):
--   (A) ALTER TABLE ... ADD COLUMN <col> ... NOT NULL DEFAULT <val> —
--       SQLite는 ALTER TABLE ADD COLUMN에 NOT NULL을 주려면 반드시
--       DEFAULT가 있어야 한다(빈 테이블이어도 이 문법 제약은 동일하게
--       적용된다). 실제로 backfill될 기존 행이 0건이므로 이 DEFAULT는
--       순수하게 DDL 문법을 만족시키기 위한 것이며, 이후 모든 실제
--       INSERT는 서비스 레이어가 항상 명시적으로 값을 지정한다
--       (Model의 Python 레벨에는 해당 default가 없다 — nullable=False만
--       있다). 20260805_00/20260814_01에서 이미 확립된 패턴이다.
--   (B) UNIQUE 제약 범위가 바뀌는 테이블(decision_evaluations/
--       funding_accounts/funding_holds/supplier_payments)은 SQLite가
--       테이블 레벨 UNIQUE/컬럼 nullable을 ALTER TABLE만으로 바꿀 수
--       없어 재생성이 필요하다 — "<이름>_new로 CREATE → 기존 테이블
--       DROP → RENAME" 기법을 쓴다(단순 DROP 후 같은 이름으로 다시
--       CREATE하지 않는다 — 공식 MigrationRunner의
--       _extract_superseded_targets()가 이 패턴을 인식하도록 Gate R13
--       에서 이미 보강되어 있다).
--
-- CoupangPolicySet/CoupangPolicyRule/DecisionPolicy(전역 기본 템플릿
-- 자체)는 이번 Migration 대상이 아니다(전역 플랫폼 설정으로 유지).
-- ProductCandidate/ProductCandidateEvidence의 GLOBAL 발견 카탈로그
-- 성격 자체도 바뀌지 않는다(PRIVATE는 추가 옵션일 뿐 GLOBAL을
-- 대체하지 않는다).
--
-- 이 Migration은 실제 homez.db에 적용하지 않는다. 임시 SQLite 파일
-- (기존 17개 Migration을 순서대로 적용해 재현한 스키마)에서만
-- 검증했다(tests/test_gate2_tenant_isolation_hardening_migration.py).

BEGIN;

-- ====================================================
-- 1) company_decision_policies — 신규 테이블(회사별 적용 정책)
-- ====================================================

CREATE TABLE company_decision_policies (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	policy_set_id VARCHAR(100) NOT NULL,
	policy_version VARCHAR(50) NOT NULL,
	source_reference VARCHAR(500) NOT NULL,
	status VARCHAR(20) NOT NULL,
	is_complete BOOLEAN NOT NULL,
	is_active BOOLEAN NOT NULL,
	axis_weights_json TEXT NOT NULL,
	min_approve_total_score NUMERIC(9, 4) NOT NULL,
	min_confidence_for_recommendation NUMERIC(9, 4) NOT NULL,
	checked_at DATETIME NOT NULL,
	effective_at DATETIME NOT NULL,
	expires_at DATETIME,
	base_policy_id INTEGER,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_company_decision_policies_company_policy_set UNIQUE (company_id, policy_set_id)
);

CREATE INDEX ix_company_decision_policies_company_id ON company_decision_policies (company_id);
CREATE INDEX ix_company_decision_policies_id ON company_decision_policies (id);
CREATE INDEX ix_company_decision_policies_is_active ON company_decision_policies (is_active);
CREATE INDEX ix_company_decision_policies_status ON company_decision_policies (status);

-- ====================================================
-- 2) decision_evaluations — 재생성(policy_id nullable 전환 +
--    company_policy_id/policy_source 추가)
-- ====================================================
--
-- 기존 UNIQUE(company_id, idempotency_key)는 그대로 유지한다(Gate R13
-- 에서 이미 회사별로 분리됨) — 이번 변경은 정책 참조 컬럼만 바꾼다.

CREATE TABLE decision_evaluations_new (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	candidate_id INTEGER NOT NULL,
	policy_id INTEGER,
	company_policy_id INTEGER,
	policy_source VARCHAR(20) NOT NULL,
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
CREATE INDEX ix_decision_evaluations_company_policy_id ON decision_evaluations (company_policy_id);
CREATE INDEX ix_decision_evaluations_id ON decision_evaluations (id);
CREATE INDEX ix_decision_evaluations_input_fingerprint ON decision_evaluations (input_fingerprint);
CREATE INDEX ix_decision_evaluations_policy_id ON decision_evaluations (policy_id);
CREATE INDEX ix_decision_evaluations_recommendation ON decision_evaluations (recommendation);
CREATE INDEX ix_decision_evaluations_status ON decision_evaluations (status);

-- ====================================================
-- 3) product_candidates — company_id 없음, visibility/owner_company_id
--    추가(비공개 후보 지원, 단순 ADD COLUMN)
-- ====================================================

ALTER TABLE product_candidates ADD COLUMN visibility VARCHAR(20) NOT NULL DEFAULT 'GLOBAL';
ALTER TABLE product_candidates ADD COLUMN owner_company_id INTEGER;

CREATE INDEX ix_product_candidates_owner_company_id ON product_candidates (owner_company_id);
CREATE INDEX ix_product_candidates_visibility ON product_candidates (visibility);

-- ====================================================
-- 4) product_candidate_selections — memo 추가(최신 메모 현재-상태
--    투영, 단순 ADD COLUMN)
-- ====================================================

ALTER TABLE product_candidate_selections ADD COLUMN memo VARCHAR(1000);

-- ====================================================
-- 5) funding_accounts — company_id nullable→NOT NULL 전환(재생성)
-- ====================================================
--
-- UNIQUE(company_id)는 컬럼 레벨 unique=True로 선언되어 있어(테이블
-- CONSTRAINT가 아니라 별도 UNIQUE INDEX) 재생성 후 인덱스로 다시
-- 만든다.

CREATE TABLE funding_accounts_new (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	total_funding FLOAT NOT NULL,
	held_amount FLOAT NOT NULL,
	currency VARCHAR(10) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

DROP TABLE funding_accounts;

ALTER TABLE funding_accounts_new RENAME TO funding_accounts;

CREATE UNIQUE INDEX ix_funding_accounts_company_id ON funding_accounts (company_id);
CREATE INDEX ix_funding_accounts_id ON funding_accounts (id);

-- ====================================================
-- 6) funding_holds — company_id 추가 + idempotency_key UNIQUE를
--    (company_id, idempotency_key) 복합으로 전환(재생성)
-- ====================================================

CREATE TABLE funding_holds_new (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	account_id INTEGER NOT NULL,
	order_id INTEGER NOT NULL,
	purchase_id INTEGER,
	amount FLOAT NOT NULL,
	status VARCHAR(20) NOT NULL,
	idempotency_key VARCHAR(100) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_funding_holds_idempotency UNIQUE (company_id, idempotency_key)
);

DROP TABLE funding_holds;

ALTER TABLE funding_holds_new RENAME TO funding_holds;

CREATE INDEX ix_funding_holds_account_id ON funding_holds (account_id);
CREATE INDEX ix_funding_holds_company_id ON funding_holds (company_id);
CREATE INDEX ix_funding_holds_id ON funding_holds (id);
CREATE INDEX ix_funding_holds_order_id ON funding_holds (order_id);
CREATE INDEX ix_funding_holds_purchase_id ON funding_holds (purchase_id);
CREATE INDEX ix_funding_holds_status ON funding_holds (status);

-- ====================================================
-- 7) supplier_payments — company_id 추가 + idempotency_key UNIQUE를
--    (company_id, idempotency_key) 복합으로 전환(재생성, purchase_id
--    단독 UNIQUE는 업무 불변식이라 그대로 유지)
-- ====================================================

CREATE TABLE supplier_payments_new (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	purchase_id INTEGER NOT NULL,
	order_id INTEGER NOT NULL,
	supplier_id INTEGER NOT NULL,
	account_id INTEGER NOT NULL,
	hold_id INTEGER NOT NULL,
	amount FLOAT NOT NULL,
	status VARCHAR(20) NOT NULL,
	paid_at DATETIME NOT NULL,
	idempotency_key VARCHAR(100) NOT NULL,
	memo VARCHAR(500),
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_supplier_payments_idempotency UNIQUE (company_id, idempotency_key),
	CONSTRAINT uq_supplier_payments_purchase UNIQUE (purchase_id)
);

DROP TABLE supplier_payments;

ALTER TABLE supplier_payments_new RENAME TO supplier_payments;

CREATE INDEX ix_supplier_payments_account_id ON supplier_payments (account_id);
CREATE INDEX ix_supplier_payments_company_id ON supplier_payments (company_id);
CREATE INDEX ix_supplier_payments_hold_id ON supplier_payments (hold_id);
CREATE INDEX ix_supplier_payments_id ON supplier_payments (id);
CREATE INDEX ix_supplier_payments_order_id ON supplier_payments (order_id);
CREATE INDEX ix_supplier_payments_purchase_id ON supplier_payments (purchase_id);
CREATE INDEX ix_supplier_payments_status ON supplier_payments (status);
CREATE INDEX ix_supplier_payments_supplier_id ON supplier_payments (supplier_id);

-- ====================================================
-- 8) funding_ledgers — company_id 추가(단순 ADD COLUMN, UNIQUE 범위
--    변경 없음 — app/domains/funding/model.py 상단 주석 참고)
-- ====================================================

ALTER TABLE funding_ledgers ADD COLUMN company_id INTEGER NOT NULL DEFAULT 0;

CREATE INDEX ix_funding_ledgers_company_id ON funding_ledgers (company_id);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로
-- 실행, 단 이 rollback은 이번 Migration 이전 원본 스키마로 완전히
-- 되돌린다는 뜻이며 그 사이 생성된 신규 컬럼/테이블 데이터는 버려진다
-- 는 점을 반드시 인지해야 한다. DROP TABLE 방식이므로 rollback
-- 시점에 각 테이블이 실제로 비어 있는지 먼저 확인하라):
-- BEGIN;
-- DROP INDEX ix_funding_ledgers_company_id;
-- ALTER TABLE funding_ledgers DROP COLUMN company_id;
-- DROP TABLE supplier_payments;
-- CREATE TABLE supplier_payments ( id INTEGER NOT NULL, purchase_id INTEGER NOT NULL, order_id INTEGER NOT NULL, supplier_id INTEGER NOT NULL, account_id INTEGER NOT NULL, hold_id INTEGER NOT NULL, amount FLOAT NOT NULL, status VARCHAR(20) NOT NULL, paid_at DATETIME NOT NULL, idempotency_key VARCHAR(100) NOT NULL, memo VARCHAR(500), created_at DATETIME NOT NULL, PRIMARY KEY (id), CONSTRAINT uq_supplier_payments_idempotency UNIQUE (idempotency_key), CONSTRAINT uq_supplier_payments_purchase UNIQUE (purchase_id) );
-- CREATE INDEX ix_supplier_payments_account_id ON supplier_payments (account_id);
-- CREATE INDEX ix_supplier_payments_hold_id ON supplier_payments (hold_id);
-- CREATE INDEX ix_supplier_payments_id ON supplier_payments (id);
-- CREATE INDEX ix_supplier_payments_order_id ON supplier_payments (order_id);
-- CREATE INDEX ix_supplier_payments_purchase_id ON supplier_payments (purchase_id);
-- CREATE INDEX ix_supplier_payments_status ON supplier_payments (status);
-- CREATE INDEX ix_supplier_payments_supplier_id ON supplier_payments (supplier_id);
-- DROP TABLE funding_holds;
-- CREATE TABLE funding_holds ( id INTEGER NOT NULL, account_id INTEGER NOT NULL, order_id INTEGER NOT NULL, purchase_id INTEGER, amount FLOAT NOT NULL, status VARCHAR(20) NOT NULL, idempotency_key VARCHAR(100) NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id), CONSTRAINT uq_funding_holds_idempotency UNIQUE (idempotency_key) );
-- CREATE INDEX ix_funding_holds_account_id ON funding_holds (account_id);
-- CREATE INDEX ix_funding_holds_id ON funding_holds (id);
-- CREATE INDEX ix_funding_holds_order_id ON funding_holds (order_id);
-- CREATE INDEX ix_funding_holds_purchase_id ON funding_holds (purchase_id);
-- CREATE INDEX ix_funding_holds_status ON funding_holds (status);
-- DROP TABLE funding_accounts;
-- CREATE TABLE funding_accounts ( id INTEGER NOT NULL, company_id INTEGER, total_funding FLOAT NOT NULL, held_amount FLOAT NOT NULL, currency VARCHAR(10) NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id) );
-- CREATE UNIQUE INDEX ix_funding_accounts_company_id ON funding_accounts (company_id);
-- CREATE INDEX ix_funding_accounts_id ON funding_accounts (id);
-- ALTER TABLE product_candidate_selections DROP COLUMN memo;
-- DROP INDEX ix_product_candidates_visibility;
-- DROP INDEX ix_product_candidates_owner_company_id;
-- ALTER TABLE product_candidates DROP COLUMN owner_company_id;
-- ALTER TABLE product_candidates DROP COLUMN visibility;
-- DROP TABLE decision_evaluations;
-- CREATE TABLE decision_evaluations ( id INTEGER NOT NULL, company_id INTEGER NOT NULL, candidate_id INTEGER NOT NULL, policy_id INTEGER NOT NULL, policy_version VARCHAR(50) NOT NULL, input_snapshot_json TEXT NOT NULL, input_fingerprint VARCHAR(64) NOT NULL, total_score NUMERIC(9, 4) NOT NULL, confidence NUMERIC(9, 4) NOT NULL, recommendation VARCHAR(30) NOT NULL, recommendation_reason VARCHAR(1000) NOT NULL, blocked_by_safety BOOLEAN NOT NULL, safety_block_reason VARCHAR(500), status VARCHAR(20) NOT NULL, idempotency_key VARCHAR(160) NOT NULL, created_by VARCHAR(50) NOT NULL, evaluator_kind VARCHAR(50) NOT NULL, evaluator_version VARCHAR(50) NOT NULL, created_at DATETIME NOT NULL, PRIMARY KEY (id), CONSTRAINT uq_decision_evaluations_idempotency UNIQUE (company_id, idempotency_key) );
-- CREATE INDEX ix_decision_evaluations_candidate_id ON decision_evaluations (candidate_id);
-- CREATE INDEX ix_decision_evaluations_company_id ON decision_evaluations (company_id);
-- CREATE INDEX ix_decision_evaluations_id ON decision_evaluations (id);
-- CREATE INDEX ix_decision_evaluations_input_fingerprint ON decision_evaluations (input_fingerprint);
-- CREATE INDEX ix_decision_evaluations_policy_id ON decision_evaluations (policy_id);
-- CREATE INDEX ix_decision_evaluations_recommendation ON decision_evaluations (recommendation);
-- CREATE INDEX ix_decision_evaluations_status ON decision_evaluations (status);
-- DROP TABLE company_decision_policies;
-- COMMIT;
