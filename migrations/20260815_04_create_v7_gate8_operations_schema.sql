-- Purpose: V7 Gate 8(2026-08-15) — Gate Y(2026-08-12)에서 구현한 백업/
-- 복원/알림센터/업데이트 공지 4개 도메인의 실제 테이블 Migration.
-- Source of Truth: app/domains/backup/model.py,
-- app/domains/restore/model.py, app/domains/notification_center/model.py,
-- app/domains/update/model.py
--
-- 배경(Gate 8 감사 중 발견): Gate Y가 backup/restore/notification_center/
-- update 4개 도메인의 SQLAlchemy Model+Service+Router를 전부 구현하고
-- 각 도메인 자체 테스트(80개)까지 전부 통과시켰지만, 그 테스트들은
-- 전부 `Base.metadata.create_all()`로 임시 DB를 만들어 검증했다 —
-- 실제 운영 DB에 적용되는 `migrations/*.sql` 파일은 단 하나도
-- 작성되지 않았다. 즉 이 4개 도메인은 정식 Migration 경로로는 실제
-- homez.db에 테이블이 전혀 생기지 않는 상태였다(운영 DB에 적용했다면
-- POST /backups 등 모든 엔드포인트가 "no such table"로 즉시 실패했을
-- 것). Gate 8이 Gate 3~7 신규 테이블까지 백업/진단 범위에 포함되는지
-- 재확인하는 과정에서 발견했다 — 백업 엔진 자체(SQLite 파일 단위
-- 온라인 백업)는 스키마와 무관하게 이미 모든 테이블을 포괄하므로
-- Gate 3~7 신규 테이블 커버리지 자체는 문제가 없었지만, 그보다 상위
-- 전제인 "백업 엔진 자신의 테이블이 운영 DB에 존재하는가"가 깨져
-- 있었다.
--
-- 1) backup_records — 백업 이력(Gate Y-1, 성공만 기록).
-- 2) restore_attempts — 복원 시도 이력(Gate Y-2, 성공/실패 모두 기록).
-- 3) notifications / notification_reads — 앱 내부 알림 센터(Gate Y-3,
--    회사 공지는 notification_reads로 사용자별 읽음 상태를 분리).
-- 4) update_notices — 업데이트 공지(Gate Y-4, 서명 매니페스트 자동
--    검증은 여전히 미구현 — docs/adr/0002-update-manifest-signing-
--    design.md 참고).
--
-- 이 Migration은 실제 homez.db에 적용하지 않는다. 임시 SQLite 파일
-- (기존 23개 Migration을 순서대로 적용해 재현한 스키마)에서만 검증
-- 했다(tests/test_gate8_operations_schema_migration.py). CREATE TABLE
-- 문은 SQLAlchemy CreateTable(model.__table__).compile(dialect=sqlite)
-- 출력을 그대로 옮겨 Model↔DDL 불일치 가능성을 원천 차단했다.

BEGIN;

-- ====================================================
-- 1) backup_records
-- ====================================================

CREATE TABLE backup_records (
	id INTEGER NOT NULL,
	file_path VARCHAR(500) NOT NULL,
	file_size_bytes BIGINT NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	integrity_check_result VARCHAR(50) NOT NULL,
	trigger_source VARCHAR(50) NOT NULL,
	triggered_by_user_id INTEGER,
	label VARCHAR(200),
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

-- ====================================================
-- 2) restore_attempts
-- ====================================================

CREATE TABLE restore_attempts (
	id INTEGER NOT NULL,
	source_backup_path VARCHAR(500) NOT NULL,
	target_db_path VARCHAR(500) NOT NULL,
	pre_restore_backup_path VARCHAR(500),
	status VARCHAR(20) NOT NULL,
	integrity_check_result VARCHAR(50),
	error_message TEXT,
	file_size_bytes BIGINT,
	triggered_by_user_id INTEGER,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

-- ====================================================
-- 3) notifications / notification_reads
-- ====================================================

CREATE TABLE notifications (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	user_id INTEGER,
	category VARCHAR(50) NOT NULL,
	level VARCHAR(20) NOT NULL,
	title VARCHAR(200) NOT NULL,
	message TEXT NOT NULL,
	link_path VARCHAR(200),
	is_read BOOLEAN NOT NULL,
	read_at DATETIME,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_notifications_is_read ON notifications (is_read);
CREATE INDEX ix_notifications_category ON notifications (category);
CREATE INDEX ix_notifications_created_at ON notifications (created_at);
CREATE INDEX ix_notifications_user_id ON notifications (user_id);
CREATE INDEX ix_notifications_company_id ON notifications (company_id);

CREATE TABLE notification_reads (
	id INTEGER NOT NULL,
	notification_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	read_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_notification_reads_user_id ON notification_reads (user_id);
CREATE INDEX ix_notification_reads_notification_id ON notification_reads (notification_id);

-- ====================================================
-- 4) update_notices
-- ====================================================

CREATE TABLE update_notices (
	id INTEGER NOT NULL,
	version VARCHAR(50) NOT NULL,
	title VARCHAR(200) NOT NULL,
	message TEXT NOT NULL,
	severity VARCHAR(20) NOT NULL,
	release_notes_url VARCHAR(500),
	is_active BOOLEAN NOT NULL,
	published_by_user_id INTEGER,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_update_notices_version ON update_notices (version);
CREATE INDEX ix_update_notices_created_at ON update_notices (created_at);
CREATE INDEX ix_update_notices_is_active ON update_notices (is_active);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로
-- 실행. 전부 신규 테이블이므로 단순 DROP으로 완전히 원복된다):
-- BEGIN;
-- DROP TABLE update_notices;
-- DROP TABLE notification_reads;
-- DROP TABLE notifications;
-- DROP TABLE restore_attempts;
-- DROP TABLE backup_records;
-- COMMIT;
