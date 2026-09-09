-- Purpose: 계정 복구(아이디 찾기 / 비밀번호 재설정) — recovery_codes,
-- password_reset_tokens 생성.
-- Source of Truth: app/domains/account_recovery/model.py
--
-- 대상 2개 테이블 (FK 없음 — user_id/company_id는 논리 참조 컬럼):
--   recovery_codes
--   password_reset_tokens
--
-- 목적: 2026-08-02 보안 사고(회사명 칸에 비밀번호로 추정되는 값이
-- 잘못 저장된 사고) 수습 과정에서, 로그아웃 상태에서 계정을 스스로
-- 복구할 안전한 수단이 이 시스템에 전혀 없었다는 점이 함께 확인됐다
-- (기존에는 "내 비밀번호 변경"이 로그인 상태에서만 가능했다). 이
-- Migration이 적용되어야만 복구 코드/이메일 재설정 링크/SUPER_ADMIN
-- 발급 재설정이 실제로 동작한다.
--
-- 이 Migration은 실제 homez.db에 적용하지 않는다. 임시 DB에서만
-- 검증한다.
--
-- 실행 전 필수 확인: recovery_codes, password_reset_tokens 두 테이블
-- 모두 대상 DB에 존재하지 않아야 한다(IF NOT EXISTS를 쓰지 않음 —
-- 부분 적용 상태를 그대로 실패시킨다).

BEGIN;

CREATE TABLE recovery_codes (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	company_id INTEGER,
	code_hash VARCHAR(255) NOT NULL,
	created_at DATETIME NOT NULL,
	expires_at DATETIME,
	used_at DATETIME,
	revoked_at DATETIME,
	PRIMARY KEY (id)
);

CREATE INDEX ix_recovery_codes_user_id ON recovery_codes (user_id);
CREATE INDEX ix_recovery_codes_company_id ON recovery_codes (company_id);

CREATE TABLE password_reset_tokens (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	company_id INTEGER,
	token_hash VARCHAR(64) NOT NULL,
	issued_by VARCHAR(20) NOT NULL,
	created_at DATETIME NOT NULL,
	expires_at DATETIME NOT NULL,
	used_at DATETIME,
	revoked_at DATETIME,
	PRIMARY KEY (id)
);

CREATE INDEX ix_password_reset_tokens_user_id ON password_reset_tokens (user_id);
CREATE INDEX ix_password_reset_tokens_company_id ON password_reset_tokens (company_id);
CREATE UNIQUE INDEX ix_password_reset_tokens_token_hash ON password_reset_tokens (token_hash);
CREATE INDEX ix_password_reset_tokens_expires_at ON password_reset_tokens (expires_at);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로 실행):
-- BEGIN;
-- DROP INDEX ix_recovery_codes_user_id;
-- DROP INDEX ix_recovery_codes_company_id;
-- DROP TABLE recovery_codes;
-- DROP INDEX ix_password_reset_tokens_user_id;
-- DROP INDEX ix_password_reset_tokens_company_id;
-- DROP INDEX ix_password_reset_tokens_token_hash;
-- DROP INDEX ix_password_reset_tokens_expires_at;
-- DROP TABLE password_reset_tokens;
-- COMMIT;
