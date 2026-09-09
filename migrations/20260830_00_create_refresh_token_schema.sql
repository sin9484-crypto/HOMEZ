-- Purpose: HOMEZ V7 후속 안정화 Phase 2 — Refresh Token Rotation +
-- 재사용 탐지 스키마(refresh_token_families, refresh_tokens) 생성.
-- Source of Truth: app/domains/session/model.py
--
-- 대상 2개 테이블 (FK 없음 — user_id/access_session_jti/family_id는
-- 논리 참조 컬럼):
--   refresh_token_families
--   refresh_tokens
--
-- 목적: 기존 app/core/token.py::revoke_refresh_token()은 아무 것도
-- 하지 않는 빈 스텁이었고, AuthService.refresh()는 서버 측 세션 폐기
-- 상태를 전혀 확인하지 않았다 — 로그아웃/비밀번호 변경/계정 비활성화
-- 이후에도 이미 발급된 Refresh Token으로 계속 새 Access Token을 발급
--받을 수 있는 Critical 보안 결함이었다(2026-08-30 발견). 이 스키마가
-- 적용되어야만 Refresh Token 발급 계보(family)를 서버가 추적해
-- rotation·재사용 탐지·폐기 전파를 실제로 강제한다.
--
-- 이 Migration은 실제 homez.db(개발·운영 모두)에 적용하지 않는다.
-- 임시 DB에서만 검증한다.
--
-- 실행 전 필수 확인: refresh_token_families, refresh_tokens 두 테이블
-- 모두 대상 DB에 존재하지 않아야 한다(IF NOT EXISTS를 쓰지 않음 —
-- 부분 적용 상태를 그대로 실패시킨다).

BEGIN;

CREATE TABLE refresh_token_families (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	family_id VARCHAR(64) NOT NULL,
	access_session_jti VARCHAR(64) NOT NULL,
	status VARCHAR(20) NOT NULL,
	revoked_at DATETIME,
	revoked_reason VARCHAR(200),
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_refresh_token_families_family_id ON refresh_token_families (family_id);
CREATE INDEX ix_refresh_token_families_access_session_jti ON refresh_token_families (access_session_jti);
CREATE INDEX ix_refresh_token_families_user_id ON refresh_token_families (user_id);

CREATE TABLE refresh_tokens (
	id INTEGER NOT NULL,
	family_id VARCHAR(64) NOT NULL,
	jti VARCHAR(64) NOT NULL,
	token_hash VARCHAR(64) NOT NULL,
	issued_at DATETIME NOT NULL,
	expires_at DATETIME NOT NULL,
	consumed_at DATETIME,
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_refresh_tokens_jti ON refresh_tokens (jti);
CREATE INDEX ix_refresh_tokens_expires_at ON refresh_tokens (expires_at);
CREATE INDEX ix_refresh_tokens_family_id ON refresh_tokens (family_id);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로 실행):
-- BEGIN;
-- DROP INDEX ix_refresh_tokens_family_id;
-- DROP INDEX ix_refresh_tokens_expires_at;
-- DROP INDEX ix_refresh_tokens_jti;
-- DROP TABLE refresh_tokens;
-- DROP INDEX ix_refresh_token_families_user_id;
-- DROP INDEX ix_refresh_token_families_access_session_jti;
-- DROP INDEX ix_refresh_token_families_family_id;
-- DROP TABLE refresh_token_families;
-- COMMIT;
