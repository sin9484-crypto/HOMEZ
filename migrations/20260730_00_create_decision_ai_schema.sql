-- Purpose: HOMEZ V4 Decision AI — 전체 스키마 생성.
-- Source of Truth: app/domains/decision/model.py
--
-- 대상 5개 테이블 (Model 기준, ForeignKey 없음 — 전부 논리 참조 컬럼):
--   decision_policies
--   decision_evaluations
--   decision_scores
--   decision_reviews
--   decision_audit_logs
--
-- 이번 단계(V4 Decision AI)는 AI가 상품 등록·가격 변경·재고 발주·
-- 광고 집행·자금 이동·정산 처리를 임의로 실행하지 않는다 — 평가·
-- 점수·추천·근거만 생성하고 최종 승인은 사람이 한다. 이 Migration도
-- 실제 homez.db에 적용하지 않는다. 임시 DB에서만 검증한다.
--
-- 실행 전 필수 확인: 아래 5개 테이블이 대상 DB에 하나도 존재하지 않아야
-- 한다. 일부만 존재하는 상태(부분 적용)를 이 스크립트는 감지하지 않고
-- 그대로 CREATE TABLE을 시도해 실패하도록 둔다(IF NOT EXISTS를 쓰지 않음).

BEGIN;

CREATE TABLE decision_policies (
	id INTEGER NOT NULL,
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
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_decision_policies_policy_set_id UNIQUE (policy_set_id)
);

CREATE INDEX ix_decision_policies_id ON decision_policies (id);
CREATE INDEX ix_decision_policies_is_active ON decision_policies (is_active);
CREATE INDEX ix_decision_policies_status ON decision_policies (status);

CREATE TABLE decision_evaluations (
	id INTEGER NOT NULL,
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
	CONSTRAINT uq_decision_evaluations_idempotency UNIQUE (idempotency_key)
);

CREATE INDEX ix_decision_evaluations_candidate_id ON decision_evaluations (candidate_id);
CREATE INDEX ix_decision_evaluations_id ON decision_evaluations (id);
CREATE INDEX ix_decision_evaluations_input_fingerprint ON decision_evaluations (input_fingerprint);
CREATE INDEX ix_decision_evaluations_policy_id ON decision_evaluations (policy_id);
CREATE INDEX ix_decision_evaluations_recommendation ON decision_evaluations (recommendation);
CREATE INDEX ix_decision_evaluations_status ON decision_evaluations (status);

CREATE TABLE decision_scores (
	id INTEGER NOT NULL,
	evaluation_id INTEGER NOT NULL,
	axis VARCHAR(50) NOT NULL,
	raw_score NUMERIC(9, 4) NOT NULL,
	weight NUMERIC(9, 4) NOT NULL,
	weighted_score NUMERIC(9, 4) NOT NULL,
	confidence NUMERIC(9, 4) NOT NULL,
	data_sufficient BOOLEAN NOT NULL,
	risk_flag BOOLEAN NOT NULL,
	evidence_text VARCHAR(1000) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_decision_scores_evaluation_id ON decision_scores (evaluation_id);
CREATE INDEX ix_decision_scores_id ON decision_scores (id);

CREATE TABLE decision_reviews (
	id INTEGER NOT NULL,
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
	CONSTRAINT uq_decision_reviews_idempotency UNIQUE (idempotency_key)
);

CREATE INDEX ix_decision_reviews_evaluation_id ON decision_reviews (evaluation_id);
CREATE INDEX ix_decision_reviews_id ON decision_reviews (id);

CREATE TABLE decision_audit_logs (
	id INTEGER NOT NULL,
	evaluation_id INTEGER,
	candidate_id INTEGER NOT NULL,
	event_type VARCHAR(50) NOT NULL,
	actor VARCHAR(50) NOT NULL,
	payload_summary VARCHAR(1000) NOT NULL,
	occurred_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_decision_audit_logs_candidate_id ON decision_audit_logs (candidate_id);
CREATE INDEX ix_decision_audit_logs_evaluation_id ON decision_audit_logs (evaluation_id);
CREATE INDEX ix_decision_audit_logs_id ON decision_audit_logs (id);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로 실행):
-- BEGIN;
-- DROP INDEX ix_decision_audit_logs_candidate_id;
-- DROP INDEX ix_decision_audit_logs_evaluation_id;
-- DROP INDEX ix_decision_audit_logs_id;
-- DROP TABLE decision_audit_logs;
-- DROP INDEX ix_decision_reviews_evaluation_id;
-- DROP INDEX ix_decision_reviews_id;
-- DROP TABLE decision_reviews;
-- DROP INDEX ix_decision_scores_evaluation_id;
-- DROP INDEX ix_decision_scores_id;
-- DROP TABLE decision_scores;
-- DROP INDEX ix_decision_evaluations_candidate_id;
-- DROP INDEX ix_decision_evaluations_id;
-- DROP INDEX ix_decision_evaluations_input_fingerprint;
-- DROP INDEX ix_decision_evaluations_policy_id;
-- DROP INDEX ix_decision_evaluations_recommendation;
-- DROP INDEX ix_decision_evaluations_status;
-- DROP TABLE decision_evaluations;
-- DROP INDEX ix_decision_policies_id;
-- DROP INDEX ix_decision_policies_is_active;
-- DROP INDEX ix_decision_policies_status;
-- DROP TABLE decision_policies;
-- COMMIT;
