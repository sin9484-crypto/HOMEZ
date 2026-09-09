-- Purpose: HOMEZ 사용자 운영 기준 11번(개인정보·보안) — "비밀번호
-- 반복 오류나 이상 접근이 발견되면 로그인을 잠시 차단하고 사용자에게
-- 알린다." 감사(docs/HOMEZ_USER_OPERATION_SETTINGS_AUDIT_20260909.md
-- 11-18)에서 이 기준이 완전히 미구현임을 확인했다 —
-- app/domains/user/repository.py::increment_failed_login이 "실제 DB에
-- failed_login_count 컬럼이 없어 no-op"이라고 스스로 명시하고 있었다.
-- Source of Truth: app/domains/user/model.py, app/domains/user/repository.py
--
-- users 테이블에 3개 컬럼을 추가한다(전부 nullable 또는 기본값 있음
-- — 기존 행에 영향 없음):
--   failed_login_count   : 연속 로그인 실패 횟수. 성공 시 0으로 초기화.
--   locked_until         : 이 시각까지는 로그인을 거부한다(NULL이면
--                          잠김 아님). 만료되면 다음 로그인 시도에서
--                          자연스럽게 해제된다(별도 배치 불필요).
--   last_failed_login_at : 마지막 실패 시각(관측·감사용, 잠금 판단에는
--                          locked_until만 사용한다).
--
-- 개인정보·비밀정보는 이 컬럼들에 전혀 담기지 않는다(횟수·시각뿐).
-- 완전히 새로운 컬럼 추가만 있고(ALTER TABLE ADD COLUMN), 기존
-- 컬럼·데이터는 전혀 건드리지 않는다.
--
-- 승인 범위: 이 Migration 초안 작성과 임시 SQLite 파일 검증까지.
-- 실제 homez.db 적용은 별도 승인 후에만 진행한다(HOMEZ V7 개인 베타
-- P0~P9 연속 구현 지시서, "3. 절대 중단 경계" — 실제 업무 DB
-- Migration 적용은 이번 연속 승인 범위 밖).

BEGIN;

ALTER TABLE users ADD COLUMN failed_login_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN locked_until DATETIME;
ALTER TABLE users ADD COLUMN last_failed_login_at DATETIME;

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로
-- 실행. SQLite는 ALTER TABLE DROP COLUMN을 3.35.0+에서 지원한다.
-- 컬럼을 드롭하면 그 시점까지 쌓인 실패횟수·잠금 이력은 사라진다 —
-- 로그인 자체의 가용성에는 영향 없다, is_active/password는 이
-- Migration이 전혀 건드리지 않았기 때문이다):
-- BEGIN;
-- ALTER TABLE users DROP COLUMN last_failed_login_at;
-- ALTER TABLE users DROP COLUMN locked_until;
-- ALTER TABLE users DROP COLUMN failed_login_count;
-- COMMIT;
