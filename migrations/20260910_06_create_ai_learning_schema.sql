-- Purpose: HOMEZ 사용자 운영 기준(HOMEZ_USER_OPERATION_SETTINGS.md)
-- 12번 — "AI가 추천한 상품, 추천 이유와 점수, 사용자 승인·거절,
-- 실제 판매량·마진·품절·취소·반품·배송 지연과 예측 차이를 저장한다",
-- "학습 가능한 데이터셋을 운영 DB와 분리한다", "초기 학습 검토는
-- 최소 50건의 완료 주문부터 시작... 누적 주문이 1,000건을 넘으면
-- 최소 100건으로 상향." Phase 12 구현.
-- Source of Truth: app/domains/ai_learning/model.py
--                   (DecisionOutcome, LearningDatasetRecord,
--                    ModelCandidate, ModelCandidateStatusEvent)
--
-- app/domains/decision(DecisionEvaluation/DecisionReview)의 어떤
-- 테이블도 수정하지 않는다 — evaluation_id는 논리 참조(FK 없음)다.
-- LearningDatasetRecord는 그 운영 테이블들의 특정 시점 스냅샷을
-- 복사해 저장하는 완전히 독립된 테이블이다("학습 가능한 데이터셋을
-- 운영 DB와 분리한다"의 최소 구현 — 같은 물리 DB 파일 안이지만
-- 참조가 아니라 복사이므로, 운영 테이블이 나중에 바뀌어도 이미
-- export된 학습 행은 영향받지 않는다).
--
-- 완전히 새로운 테이블만 추가한다(ALTER 없음) — 기존 테이블은 전혀
-- 건드리지 않는다.

BEGIN;

CREATE TABLE decision_outcomes (
    id INTEGER NOT NULL PRIMARY KEY,
    company_id INTEGER NOT NULL,
    evaluation_id INTEGER NOT NULL,
    order_id INTEGER,
    actual_sales_amount FLOAT,
    actual_margin_amount FLOAT,
    stockout BOOLEAN NOT NULL DEFAULT 0,
    cancelled BOOLEAN NOT NULL DEFAULT 0,
    returned BOOLEAN NOT NULL DEFAULT 0,
    shipping_delayed BOOLEAN NOT NULL DEFAULT 0,
    recorded_by INTEGER,
    recorded_at DATETIME NOT NULL,
    UNIQUE (evaluation_id)
);

CREATE INDEX ix_decision_outcomes_id ON decision_outcomes (id);
CREATE INDEX ix_decision_outcomes_company_id ON decision_outcomes (company_id);
CREATE INDEX ix_decision_outcomes_evaluation_id ON decision_outcomes (evaluation_id);
CREATE INDEX ix_decision_outcomes_order_id ON decision_outcomes (order_id);
CREATE INDEX ix_decision_outcomes_recorded_at ON decision_outcomes (recorded_at);

CREATE TABLE learning_dataset_records (
    id INTEGER NOT NULL PRIMARY KEY,
    company_id INTEGER NOT NULL,
    evaluation_id INTEGER NOT NULL,
    input_snapshot_json TEXT NOT NULL,
    policy_version VARCHAR(50),
    recommendation VARCHAR(50),
    recommendation_reason TEXT,
    user_decision VARCHAR(20),
    actual_sales_amount FLOAT,
    actual_margin_amount FLOAT,
    stockout BOOLEAN NOT NULL DEFAULT 0,
    cancelled BOOLEAN NOT NULL DEFAULT 0,
    returned BOOLEAN NOT NULL DEFAULT 0,
    shipping_delayed BOOLEAN NOT NULL DEFAULT 0,
    exported_at DATETIME NOT NULL,
    UNIQUE (evaluation_id)
);

CREATE INDEX ix_learning_dataset_records_id ON learning_dataset_records (id);
CREATE INDEX ix_learning_dataset_records_company_id ON learning_dataset_records (company_id);
CREATE INDEX ix_learning_dataset_records_evaluation_id ON learning_dataset_records (evaluation_id);
CREATE INDEX ix_learning_dataset_records_exported_at ON learning_dataset_records (exported_at);

CREATE TABLE model_candidates (
    id INTEGER NOT NULL PRIMARY KEY,
    company_id INTEGER NOT NULL,
    name VARCHAR(200) NOT NULL,
    version VARCHAR(50) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'DRAFT',
    sample_size_used INTEGER,
    offline_eval_summary TEXT,
    regression_comparison_summary TEXT,
    created_by INTEGER NOT NULL,
    approved_by INTEGER,
    approved_at DATETIME,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);

CREATE INDEX ix_model_candidates_id ON model_candidates (id);
CREATE INDEX ix_model_candidates_company_id ON model_candidates (company_id);
CREATE INDEX ix_model_candidates_status ON model_candidates (status);

CREATE TABLE model_candidate_status_events (
    id INTEGER NOT NULL PRIMARY KEY,
    company_id INTEGER NOT NULL,
    model_candidate_id INTEGER NOT NULL,
    previous_status VARCHAR(30),
    new_status VARCHAR(30) NOT NULL,
    reason VARCHAR(500),
    triggered_by INTEGER,
    created_at DATETIME NOT NULL
);

CREATE INDEX ix_model_candidate_status_events_id ON model_candidate_status_events (id);
CREATE INDEX ix_model_candidate_status_events_company_id ON model_candidate_status_events (company_id);
CREATE INDEX ix_model_candidate_status_events_model_candidate_id ON model_candidate_status_events (model_candidate_id);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로
-- 실행. 완전히 새로운 테이블이라 DROP TABLE로 데이터 손실 없이
-- 되돌릴 수 있다):
-- BEGIN;
-- DROP TABLE model_candidate_status_events;
-- DROP TABLE model_candidates;
-- DROP TABLE learning_dataset_records;
-- DROP TABLE decision_outcomes;
-- COMMIT;
