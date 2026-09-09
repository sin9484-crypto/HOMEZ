-- Purpose: 회원가입/승인 워크플로 — user_registration_requests,
-- invitation_codes 생성.
-- Source of Truth: app/domains/account_registration/model.py
--
-- 대상 2개 테이블 (FK 없음 — company_id/user_id 등은 논리 참조 컬럼):
--   user_registration_requests
--   invitation_codes
--
-- 목적: 가입자가 승인 전에는 앱에 접근할 수 없어야 한다는 요구를
-- 구조적으로 강제한다. users.is_active/role_id를 상태의 유일한
-- 표현으로 쓰지 않고, 이 테이블이 명시적 상태 기계(PENDING_APPROVAL/
-- APPROVED/REJECTED/SUSPENDED)와 감사 가능한 결정 이력을 담당한다.
--
-- 이 Migration은 실제 homez.db에 적용하지 않는다. 임시 DB에서만
-- 검증한다. 이 기능이 실제로 쓰이려면 신규 Permission 5건(
-- USER_REGISTRATION_VIEW/USER_ACCESS_APPROVE/USER_ACCESS_REJECT/
-- USER_ACCESS_SUSPEND/USER_ROLE_ASSIGN)을 permissions 테이블에 별도
-- 데이터 시딩으로 추가해야 한다(이 Migration은 스키마만 다룬다 —
-- 기존 관례상 데이터 시딩은 app/database/seed_role_permission.py류
-- 스크립트가 담당하며 Migration 파일에는 넣지 않는다).
--
-- 실행 전 필수 확인: user_registration_requests, invitation_codes 두
-- 테이블 모두 대상 DB에 존재하지 않아야 한다(IF NOT EXISTS를 쓰지
-- 않음 — 부분 적용 상태를 그대로 실패시킨다).

BEGIN;

CREATE TABLE user_registration_requests (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	status VARCHAR(20) NOT NULL,
	requested_at DATETIME NOT NULL,
	decided_at DATETIME,
	decided_by_user_id INTEGER,
	rejection_reason_code VARCHAR(50),
	granted_role_id INTEGER,
	invitation_code_id INTEGER,
	PRIMARY KEY (id)
);

CREATE INDEX ix_user_registration_requests_company_id ON user_registration_requests (company_id);
CREATE INDEX ix_user_registration_requests_user_id ON user_registration_requests (user_id);
CREATE INDEX ix_user_registration_requests_status ON user_registration_requests (status);
CREATE INDEX ix_user_registration_requests_invitation_code_id ON user_registration_requests (invitation_code_id);

CREATE TABLE invitation_codes (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	code_hash VARCHAR(64) NOT NULL,
	created_by_user_id INTEGER NOT NULL,
	max_role_code VARCHAR(50) NOT NULL,
	created_at DATETIME NOT NULL,
	expires_at DATETIME NOT NULL,
	max_uses INTEGER NOT NULL,
	used_count INTEGER NOT NULL,
	revoked_at DATETIME,
	PRIMARY KEY (id)
);

CREATE INDEX ix_invitation_codes_company_id ON invitation_codes (company_id);
CREATE UNIQUE INDEX ix_invitation_codes_code_hash ON invitation_codes (code_hash);
CREATE INDEX ix_invitation_codes_expires_at ON invitation_codes (expires_at);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로 실행):
-- BEGIN;
-- DROP INDEX ix_user_registration_requests_company_id;
-- DROP INDEX ix_user_registration_requests_user_id;
-- DROP INDEX ix_user_registration_requests_status;
-- DROP INDEX ix_user_registration_requests_invitation_code_id;
-- DROP TABLE user_registration_requests;
-- DROP INDEX ix_invitation_codes_company_id;
-- DROP INDEX ix_invitation_codes_code_hash;
-- DROP INDEX ix_invitation_codes_expires_at;
-- DROP TABLE invitation_codes;
-- COMMIT;
