-- Purpose: Gate F-2(2026-08-14) — Desktop 재시작마다 무작위 포트로 origin이
-- 바뀌어 localStorage 기반 언어/가이드 진행 상태가 사라지는 문제를 해소하기
-- 위한 서버 사용자 설정 저장소.
-- Source of Truth: app/domains/user_settings/model.py::UserSetting
--
-- 신규 테이블 1개만 추가한다(순수 CREATE TABLE + CREATE INDEX, 기존 어떤
-- 테이블·컬럼도 건드리지 않음).
--
-- DDL은 SQLAlchemy CreateTable/CreateIndex를 sqlite dialect로 컴파일한
-- 결과를 그대로 옮겼다(migration-safety 컨벤션) — Model에 없는
-- CHECK/DEFAULT/CASCADE/trigger/FK를 추가하지 않았다.
--
-- 허용된 setting_key(locale/guide_progress/ui_preferences/
-- notification_preferences)는 app/domains/user_settings/schema.py의
-- ALLOWED_SETTING_KEYS가 애플리케이션 레벨에서만 강제한다 — DB에는 CHECK
-- 제약을 두지 않는다(Model에 선언되지 않은 제약을 SQL에 임의로 추가하지
-- 않는다는 기존 컨벤션과 동일).
--
-- 이 Migration은 실제 homez.db에 적용하지 않는다. 임시 DB 리허설로만
-- 검증했다(별도 승인 후 Live Gate에서만 적용 — docs/live_gate 계획 참고).

BEGIN;

CREATE TABLE user_settings (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	setting_key VARCHAR(50) NOT NULL,
	setting_value TEXT NOT NULL,
	schema_version INTEGER NOT NULL,
	version INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_user_settings_user_key UNIQUE (user_id, setting_key)
);

CREATE INDEX ix_user_settings_id ON user_settings (id);
CREATE INDEX ix_user_settings_company_id ON user_settings (company_id);
CREATE INDEX ix_user_settings_user_id ON user_settings (user_id);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로 실행):
-- BEGIN;
-- DROP INDEX ix_user_settings_user_id;
-- DROP INDEX ix_user_settings_company_id;
-- DROP INDEX ix_user_settings_id;
-- DROP TABLE user_settings;
-- COMMIT;
