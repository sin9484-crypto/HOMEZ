-- Purpose: 감사로그(audit_logs) 시각 컬럼 추가(2026-08-21 작업 6) —
-- 이 테이블은 SQLAlchemy Model이 없는 레거시 raw-SQL 테이블이다
-- (app/core/audit_db.py::write_audit_log()가 직접 INSERT). 기존
-- 컬럼은 id/company_id/user_id/action/entity/entity_id/description/
-- ip_address뿐이고 시각 컬럼이 전혀 없었다.
--
-- 기존 행(과거에 기록된 행)은 시각을 추정하거나 임의로 backfill하지
-- 않는다 — DEFAULT 없이 nullable로만 추가한다. 이 컬럼이 생긴 이후
-- app/core/audit_db.py::write_audit_log()가 새로 기록하는 행부터만
-- UTC ISO-8601 문자열을 채운다(그 함수는 이 Migration이 아직 적용
--되지 않은 DB에서도 안전하게 동작하도록 PRAGMA table_info로 컬럼
-- 존재 여부를 매번 직접 확인한다 — 이 Migration을 적용하기 전에도
-- 기존 감사로그 쓰기 동작은 전혀 바뀌지 않는다).
--
-- 기존 감사로그 내용·행은 이 Migration이 전혀 변경하지 않는다
-- (ADD COLUMN 한 문장뿐 — UPDATE/DELETE 없음).
--
-- 이 Migration은 실제 homez.db(개발·운영 모두)에 적용하지 않는다.
-- 임시 SQLite 파일에서만 검증했다.

BEGIN;

ALTER TABLE audit_logs ADD COLUMN created_at TEXT;

COMMIT;

-- Rollback(주석 전용, 자동 실행되지 않음 — SQLite는 컬럼 DROP에
-- 테이블 재생성이 필요하다, 기존 additive Migration들과 동일한 제약).
