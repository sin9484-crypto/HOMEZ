-- Purpose: AG-4(2026-08-21 CTO 후속 지시) — AI가 생성하는 실행
-- 제안(가격 변경안, 발주안, 재고 보충안 등)을 담는 공통 계약
-- ai_proposed_actions 1개 테이블 신규.
-- Source of Truth: app/domains/ai_governance/model.py::ProposedAction
--
-- AI는 status=DRAFT/REVIEW_REQUIRED로만 이 행을 만들 수 있다
-- (강제는 app/domains/ai_governance/proposed_action_service.py::
-- ProposedActionService.create()가 담당 — 이 스키마 자체는 문자열
-- 제약을 두지 않는다, 이 코드베이스 전역 컨벤션과 동일). APPROVED/
-- EXECUTED로의 전이는 사람의 명시적 승인만 수행한다(decided_by는
-- 항상 Router의 current_user에서만 채워진다 — 요청 바디로 자칭 불가).
--
-- 이 Migration은 실제 homez.db(개발·운영 모두)에 적용하지 않는다.
-- 임시 SQLite 파일에서만 검증했다
-- (tests/test_ai_governance_proposed_action.py).

BEGIN;

CREATE TABLE ai_proposed_actions (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	capability_code VARCHAR(60) NOT NULL,
	action_type VARCHAR(60) NOT NULL,
	target_entity VARCHAR(150) NOT NULL,
	proposed_payload_json TEXT NOT NULL,
	reason TEXT NOT NULL,
	evidence_json TEXT NOT NULL,
	risk_level VARCHAR(20) NOT NULL,
	approval_required BOOLEAN NOT NULL,
	input_fingerprint VARCHAR(64) NOT NULL,
	status VARCHAR(20) NOT NULL,
	created_by INTEGER,
	decided_by INTEGER,
	decided_at DATETIME,
	decision_reason TEXT,
	executed_at DATETIME,
	executed_reference VARCHAR(150),
	expires_at DATETIME,
	idempotency_key VARCHAR(120) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_ai_proposed_actions_company_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX ix_ai_proposed_actions_id ON ai_proposed_actions (id);
CREATE INDEX ix_ai_proposed_actions_company_id ON ai_proposed_actions (company_id);
CREATE INDEX ix_ai_proposed_actions_capability_code ON ai_proposed_actions (capability_code);
CREATE INDEX ix_ai_proposed_actions_status ON ai_proposed_actions (status);
CREATE INDEX ix_ai_proposed_actions_created_at ON ai_proposed_actions (created_at);

COMMIT;

-- Rollback(참고용 주석 — 자동 실행되지 않는다. 이 코드베이스 전역
-- 컨벤션: 실제 rollback은 항상 백업 복원 방식을 우선한다):
-- DROP TABLE ai_proposed_actions;
