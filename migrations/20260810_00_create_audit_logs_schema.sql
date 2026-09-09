-- Purpose: Gate U-2(2026-08-10) — `audit_logs` 문서화 Migration.
-- Source of Truth: 실제 homez.db에 이미 존재하는 audit_logs 테이블
-- (app/core/audit_db.py::write_audit_log()가 raw SQL로 쓰는 대상).
--
-- 이 테이블은 실제 homez.db에 이미 존재하지만, 이를 만드는 Migration
-- 파일이 저장소에 없었다(app/domains/audit/**는 아직 빈 스캐폴딩이라
-- ORM Model이 없고, 최초 생성 경로는 이력에 남아 있지 않다 — Gate S/T
-- 때부터 확인된 사실). 이 Migration은 새 설계가 아니라 실제 운영 DB에
-- 이미 존재하는 스키마를 원문 그대로(FOREIGN KEY 2개 포함) 문서화한다.
--
-- 이 저장소의 다른 Domain은 전부 FK를 쓰지 않는 컨벤션이지만, 이
-- 테이블은 그 컨벤션이 확립되기 전부터 이미 이 모양으로 실제 운영
-- DB에 존재했으므로, 임의로 FK를 제거해 실제 스키마와 다르게 만들지
-- 않는다 — Model이 없으므로 "Model에서 파생" 대신 sqlite_master의
-- 실제 DDL을 Source of Truth로 삼는다(migration-safety 컨벤션의
-- 예외적 적용, 이유는 위와 같다).
--
-- Gate U-2 리허설 결과(tests/test_audit_logs_migration.py):
--   - 빈(clean) 임시 DB: 이 파일을 그대로 적용하면 테이블이 정확히
--     이 모양으로 생성된다.
--   - 실제 homez.db 복사본: 테이블이 이미 존재하므로 MigrationRunner.
--     diagnose()가 backfill 대상으로 분류하고, reconcile_backfill()은
--     SQL을 재실행하지 않고 이력만 기록한다(no-op, 데이터 보존) — 이
--     Migration 파일의 CREATE TABLE 원문이 실제 DB의 기존 DDL과
--     정규화 비교 시 정확히 일치함을 별도로 검증한다.
--   - 스키마가 다른 경우(합성 테스트 DB로만 재현): 이 Migration은
--     자동으로 고치지 않는다 — tests/test_audit_logs_migration.py의
--     비교 로직이 즉시 실패를 보고한다.
--
-- 이 Migration은 실제 homez.db에 적용하지 않는다. 임시 DB와 실제 DB
-- 복사본으로만 검증했다(Gate U-6 계획서 대기 — 실제 적용은 별도
-- 승인이 필요하다).

BEGIN;

CREATE TABLE audit_logs (
	id INTEGER NOT NULL,
	company_id INTEGER,
	user_id INTEGER,
	action VARCHAR(100) NOT NULL,
	entity VARCHAR(100) NOT NULL,
	entity_id VARCHAR(100) NOT NULL,
	description VARCHAR(500),
	ip_address VARCHAR(50),
	PRIMARY KEY (id),
	FOREIGN KEY(company_id) REFERENCES companies (id),
	FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE INDEX ix_audit_logs_id ON audit_logs (id);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로 실행):
-- BEGIN;
-- DROP INDEX ix_audit_logs_id;
-- DROP TABLE audit_logs;
-- COMMIT;
