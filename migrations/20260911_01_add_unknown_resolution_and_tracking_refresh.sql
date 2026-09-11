-- Purpose: 운영 전 최종 검증 라운드(2026-09-11) — UNKNOWN 발주
-- 수동 확인·확정 절차, 그 append-only 감사 이벤트, 송장 재조회의
-- "마지막 조회시각/결과" 표시 컬럼.
-- Source of Truth: app/domains/purchase_task/model.py
-- (PurchaseOrderSubmissionAttempt, PurchaseOrderUnknownResolutionEvent,
-- PurchaseTaskTrackingInfo), constants.py (UnknownResolutionStatus,
-- TrackingRefreshResult)
--
-- 배경: 온채널은 sale_code 조회를 지원하지 않으므로, RESULT_UNKNOWN
-- 발주 시도는 반드시 사람이 온채널 관리자 화면을 직접 확인해
-- 확정해야 한다. 이 Migration은 그 확정 결과를 attempt 행(현재
-- 상태 1개)과 별도의 append-only 이벤트 테이블 양쪽에 기록할 수
-- 있게 한다 — 나중에 다시 확정하더라도 이전 이벤트 행은 지우거나
-- 덮어쓰지 않는다.
--
-- purchase_order_submission_attempts 신규 컬럼 5개(전부 nullable
-- 이거나 안전한 기본값 — 기존 행에 영향 없음, 기존 행은 전부
-- unknown_resolution_status='UNRESOLVED'로 시작한다. 이는 사실과
-- 다르지 않다 — 기존 RESULT_UNKNOWN 행들은 실제로 아직 아무도
-- 확정한 적이 없었다).
--
-- purchase_task_tracking_infos 신규 컬럼 2개(전부 nullable) —
-- "마지막 조회시각"은 값이 바뀌지 않은 재조회도 시각을 남겨야
-- UI가 보여줄 수 있어 별도 컬럼이 필요하다(updated_at은 실제
-- 컬럼 값이 바뀔 때만 갱신됨).
--
-- 승인 범위: 이 Migration 초안 작성과 임시 SQLite 파일 검증까지.
-- 실제 homez.db 적용은 별도 승인 후에만 진행한다.

BEGIN;

-- ====================================================
-- purchase_order_submission_attempts — 컬럼 5개 추가
-- ====================================================

ALTER TABLE purchase_order_submission_attempts ADD COLUMN unknown_resolution_status VARCHAR(30) NOT NULL DEFAULT 'UNRESOLVED';
ALTER TABLE purchase_order_submission_attempts ADD COLUMN unknown_resolved_order_code VARCHAR(200);
ALTER TABLE purchase_order_submission_attempts ADD COLUMN unknown_resolution_basis VARCHAR(500);
ALTER TABLE purchase_order_submission_attempts ADD COLUMN unknown_resolved_by INTEGER;
ALTER TABLE purchase_order_submission_attempts ADD COLUMN unknown_resolved_at DATETIME;

-- ====================================================
-- purchase_order_unknown_resolution_events — 신규 테이블(append-only)
-- ====================================================

CREATE TABLE purchase_order_unknown_resolution_events (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	connection_id INTEGER NOT NULL,
	purchase_task_id INTEGER,
	attempt_id INTEGER NOT NULL,
	resolution_status VARCHAR(30) NOT NULL,
	order_code VARCHAR(200),
	basis VARCHAR(500),
	resolved_by INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_purchase_order_unknown_resolution_events_id ON purchase_order_unknown_resolution_events (id);
CREATE INDEX ix_purchase_order_unknown_resolution_events_attempt_id ON purchase_order_unknown_resolution_events (attempt_id);
CREATE INDEX ix_purchase_order_unknown_resolution_events_purchase_task_id ON purchase_order_unknown_resolution_events (purchase_task_id);
CREATE INDEX ix_purchase_order_unknown_resolution_events_company_id ON purchase_order_unknown_resolution_events (company_id);
CREATE INDEX ix_purchase_order_unknown_resolution_events_connection_id ON purchase_order_unknown_resolution_events (connection_id);

-- ====================================================
-- purchase_task_tracking_infos — 컬럼 2개 추가
-- ====================================================

ALTER TABLE purchase_task_tracking_infos ADD COLUMN last_live_refresh_at DATETIME;
ALTER TABLE purchase_task_tracking_infos ADD COLUMN last_live_refresh_result VARCHAR(30);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로
-- 실행. SQLite는 ALTER TABLE DROP COLUMN을 3.35.0+에서 지원한다.
-- 신규 테이블은 DROP TABLE로 되돌릴 수 있다(단, 이미 쌓인 확정
-- 이력 자체는 사라진다)):
-- BEGIN;
-- DROP TABLE purchase_order_unknown_resolution_events;
-- ALTER TABLE purchase_task_tracking_infos DROP COLUMN last_live_refresh_at;
-- ALTER TABLE purchase_task_tracking_infos DROP COLUMN last_live_refresh_result;
-- ALTER TABLE purchase_order_submission_attempts DROP COLUMN unknown_resolution_status;
-- ALTER TABLE purchase_order_submission_attempts DROP COLUMN unknown_resolved_order_code;
-- ALTER TABLE purchase_order_submission_attempts DROP COLUMN unknown_resolution_basis;
-- ALTER TABLE purchase_order_submission_attempts DROP COLUMN unknown_resolved_by;
-- ALTER TABLE purchase_order_submission_attempts DROP COLUMN unknown_resolved_at;
-- COMMIT;
