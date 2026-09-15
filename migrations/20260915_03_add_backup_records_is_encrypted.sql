-- =========================================================
-- Homez OS
--
-- File : migrations/20260915_03_add_backup_records_is_encrypted.sql
--
-- 2026-09-15 전면 감사 후속(Phase 5, HOMEZ_USER_OPERATION_SETTINGS.md
-- 11번 — "실제 DB 백업 파일은 암호화하고 GitHub에 올리지 않는다").
--
-- app/domains/backup/encryption.py는 완전히 구현되어 있었지만
-- BackupService.create_backup()에 실제로 연결되어 있지 않았다(백업
-- 파일이 평문 SQLite로 저장됨 — 두 감사 문서 모두 지적한 결함).
-- 이번 라운드에서 create_backup()이 백업 생성 직후(무결성 검사·
-- SHA-256 계산 — 둘 다 평문 기준 — 이후) 실제로 암호화하도록
-- 연결했고, RestoreService도 암호화된 백업을 자동 감지해 복호화한
-- 뒤 검증·복원하도록 바꿨다.
--
-- 이 Migration은 backup_records에 is_encrypted(NOT NULL, 기본값
-- 0/false) 컬럼을 추가한다. 이 컬럼 추가 이전에 생성된 기존 백업
-- 행은 실제로 평문이었으므로 기본값 0이 사실과 일치한다(추측으로
-- 1로 채우지 않는다) — 이 Migration 적용 이후 생성되는 백업부터
-- 애플리케이션 코드가 명시적으로 1을 기록한다.
--
-- 기존 컬럼·데이터는 전혀 건드리지 않는다.
-- =========================================================

BEGIN;

ALTER TABLE backup_records ADD COLUMN is_encrypted BOOLEAN NOT NULL DEFAULT 0;

COMMIT;

-- Rollback(수동, 실제 DB 적용 시 별도 승인 후에만 사용):
-- SQLite는 컬럼 DROP을 직접 지원하지 않는다 — 되돌리려면 테이블을
-- 재생성해야 한다(homez-migration-safety 원칙에 따라 자동 실행하지
-- 않음).
