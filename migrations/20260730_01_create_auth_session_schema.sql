-- Purpose: HOMEZ Desktop 로그인 흐름 — 서버 측 세션(auth_sessions) 생성.
-- Source of Truth: app/domains/session/model.py
--
-- 대상 1개 테이블 (FK 없음 — user_id는 논리 참조 컬럼):
--   auth_sessions
--
-- 목적: 기존 JWT 인증은 상태 없이(stateless) 발급되어 자연 만료 전에는
-- 로그아웃/강제 무효화가 불가능했다(2026-07-30 확인 — /auth/logout이
-- 아무 것도 취소하지 않는 stub이었다). 이 테이블이 적용되어야만 서버가
-- 실제로 "로그아웃 시 서버 세션 폐기"/"로그아웃 후 재사용 차단"을
-- 강제한다. 적용 전에는 app/domains/session/service.py가 SCHEMA_NOT_READY로
-- 안전하게 폴백해 기존 동작(JWT 자연 만료에만 의존)을 유지한다 — 이
-- Migration 미적용 상태에서 앱이 죽거나 로그인이 막히지 않는다.
--
-- 이 Migration은 실제 homez.db에 적용하지 않는다. 임시 DB에서만
-- 검증한다.
--
-- 실행 전 필수 확인: auth_sessions 테이블이 대상 DB에 존재하지 않아야
-- 한다(IF NOT EXISTS를 쓰지 않음 — 부분 적용 상태를 그대로 실패시킨다).

BEGIN;

CREATE TABLE auth_sessions (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	jti VARCHAR(64) NOT NULL,
	issued_at DATETIME NOT NULL,
	expires_at DATETIME NOT NULL,
	revoked_at DATETIME,
	revoked_reason VARCHAR(200),
	last_seen_at DATETIME,
	is_desktop BOOLEAN NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_auth_sessions_expires_at ON auth_sessions (expires_at);
CREATE UNIQUE INDEX ix_auth_sessions_jti ON auth_sessions (jti);
CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로 실행):
-- BEGIN;
-- DROP INDEX ix_auth_sessions_expires_at;
-- DROP INDEX ix_auth_sessions_jti;
-- DROP INDEX ix_auth_sessions_user_id;
-- DROP TABLE auth_sessions;
-- COMMIT;
