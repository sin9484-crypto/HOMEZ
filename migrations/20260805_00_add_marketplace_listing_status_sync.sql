-- Purpose: HOMEZ 최종 제품화 Phase 4 — 플랫폼 등록 상태 동기화.
-- Source of Truth: app/domains/marketplace_listing/model.py
--
-- 2026-08-05 — 중요: `20260731_00_create_marketplace_fulfillment_
-- schema.sql`은 그 파일 자신의 오래된 주석과 달리 **이미 실제
-- homez.db에 적용되어 있다**(schema_migrations에 BACKFILLED로 기록,
-- checksum 3254daad9d...42ae9, 적용일 2026-08-01). 이 사실은 이번
-- Phase 4 작업 중 test_marketplace_fulfillment_migration.py의
-- RealDatabaseAppliedTestCase가 실제로 통과하는 것을 보고서야
-- 확인했다 — 이전 세션 문서(Ledger 등)의 "미적용" 기술은 그 시점
-- 이후의 실제 적용을 반영하지 못한 stale 정보였다.
--
-- 그래서 그 파일을 그 자리에서 수정하지 않는다(이미 적용된 Migration
-- 파일은 checksum이 schema_migrations에 고정되어 있어, 내용을 바꾸면
-- 다음 diagnose()에서 ChecksumMismatchError로 실제 Desktop 시작을
--막을 위험이 있다) — 대신 이 신규 파일로 순수 추가만 한다.
--
-- 대상: 기존 marketplace_listings에 컬럼 5개 추가 + 신규 테이블
-- marketplace_listing_status_events 1개 생성(provider_observed_at/
-- applied 컬럼 포함). 기존 컬럼·테이블은 전혀 건드리지 않는다
-- (ALTER TABLE ADD COLUMN만 사용, DROP/RENAME 없음).
--
-- 2026-08-05 CTO 반려 반영 — out-of-order 방어(platform_status_
-- observed_at)와 event별 반영 여부(applied) 추적을 위해 컬럼 3개를
-- 추가로 반영한다(최초 초안 대비). applied는 신규 테이블(기존 행
--없음)의 컬럼이라 backfill용 DEFAULT가 필요 없다 — Model처럼
-- DDL에는 DEFAULT를 두지 않고, 모든 실제 INSERT는 서비스 레이어가
-- 명시적으로 값을 지정한다(platform_sync_status의 ALTER TABLE
-- DEFAULT와는 성격이 다름 — 그건 기존 행 backfill이 필수였다).
--
-- 2026-08-05 CTO 반려 반영(item 4, 같은 날 추가 반영) —
-- attempt_number 컬럼 1개를 더 추가한다(재시도 감사 추적용). 같은
-- 이유로 신규 테이블 컬럼이라 DDL DEFAULT가 필요 없다.
--
-- platform_sync_status만 NOT NULL이라 SQLite ALTER TABLE 제약상
-- DEFAULT 'UNKNOWN'이 필요하다(기존 행을 backfill하기 위함 — 이 값은
-- Model의 Python 레벨 default="UNKNOWN"과 동일하므로 "Model에 없는
-- DEFAULT를 임의로 추가"하는 것이 아니다). 이후 모든 실제 쓰기는
-- 여전히 서비스 레이어가 명시적으로 값을 지정한다 — 이 DEFAULT는
-- 오직 ALTER TABLE 시점의 backfill용이다.
--
-- 이 Migration은 실제 homez.db에 적용하지 않는다. 임시 DB 리허설로만
-- 검증했다(LIVE_GATE_QUEUE 대기).

BEGIN;

ALTER TABLE marketplace_listings ADD COLUMN platform_sync_status VARCHAR(30) NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE marketplace_listings ADD COLUMN platform_raw_status VARCHAR(100);
ALTER TABLE marketplace_listings ADD COLUMN status_last_refreshed_at DATETIME;
ALTER TABLE marketplace_listings ADD COLUMN status_last_refresh_error_code VARCHAR(30);
ALTER TABLE marketplace_listings ADD COLUMN platform_status_observed_at DATETIME;

CREATE INDEX ix_marketplace_listings_platform_sync_status ON marketplace_listings (platform_sync_status);

CREATE TABLE marketplace_listing_status_events (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	listing_id INTEGER NOT NULL,
	submission_id INTEGER,
	source VARCHAR(20) NOT NULL,
	previous_status VARCHAR(30),
	normalized_status VARCHAR(30) NOT NULL,
	platform_raw_status VARCHAR(100),
	provider_observed_at DATETIME,
	error_code VARCHAR(30),
	applied BOOLEAN NOT NULL,
	attempt_number INTEGER NOT NULL,
	triggered_by INTEGER,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_marketplace_listing_status_events_company_id ON marketplace_listing_status_events (company_id);
CREATE INDEX ix_marketplace_listing_status_events_created_at ON marketplace_listing_status_events (created_at);
CREATE INDEX ix_marketplace_listing_status_events_id ON marketplace_listing_status_events (id);
CREATE INDEX ix_marketplace_listing_status_events_listing_id ON marketplace_listing_status_events (listing_id);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로 실행,
-- 단 SQLite는 DROP COLUMN을 구버전에서 지원하지 않을 수 있으므로
-- 컬럼 제거는 테이블 재생성이 필요할 수 있다 — 실제 rollback 전 별도
-- 검토 필요):
-- BEGIN;
-- DROP INDEX ix_marketplace_listing_status_events_company_id;
-- DROP INDEX ix_marketplace_listing_status_events_created_at;
-- DROP INDEX ix_marketplace_listing_status_events_id;
-- DROP INDEX ix_marketplace_listing_status_events_listing_id;
-- DROP TABLE marketplace_listing_status_events;
-- DROP INDEX ix_marketplace_listings_platform_sync_status;
-- -- ALTER TABLE ... DROP COLUMN은 SQLite 3.35.0+ 에서만 지원.
-- ALTER TABLE marketplace_listings DROP COLUMN status_last_refresh_error_code;
-- ALTER TABLE marketplace_listings DROP COLUMN status_last_refreshed_at;
-- ALTER TABLE marketplace_listings DROP COLUMN platform_raw_status;
-- ALTER TABLE marketplace_listings DROP COLUMN platform_sync_status;
-- COMMIT;
