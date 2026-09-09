-- Purpose: 이미지 저작권·상표권 증빙 저장 + 사용자 진행-선택 감사기록
-- (2026-08-28 사용자 결정). Source of Truth: app/domains/media_asset/model.py
--
-- 정책 반전 기록: 2026-08-20 3차 CTO 지시는 RIGHTS_UNVERIFIED 이미지의
-- 등록 선택·승인·공개 업로드를 하드 차단했다. 2026-08-28 사용자 결정으로
-- "증빙 미제출만으로는 어떤 기능도 차단하지 않는다"로 정책이 바뀌었다
-- (listing_wizard_service.py의 두 차단, public_hosting.py의 차단을 제거).
-- 대신 이 두 테이블이 "경고를 봤고 사용자가 계속 진행을 선택했다"는
-- 사실을 append-only로 남긴다 — 증빙 자체가 진위를 보증하지 않는다는
-- 점은 코드 주석과 화면 문구에 그대로 유지한다.
--
-- image_rights_evidence: 공급처·출처 도메인 단위 증빙(여러 상품 재사용).
-- image_rights_acknowledgements: 경고 화면에서의 사용자 선택(append-only).
--
-- 이 Migration은 실제 homez.db(개발·운영 모두)에 적용하지 않았다.
-- 임시 SQLite 파일에서만 검증한다(tests/test_media_asset_rights_evidence_migration.py).

BEGIN;

CREATE TABLE image_rights_evidence (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	supplier_id INTEGER,
	source_domain VARCHAR(255),
	evidence_type VARCHAR(50) NOT NULL,
	allowed_channels_json VARCHAR(500) NOT NULL,
	commercial_use_status VARCHAR(20) NOT NULL,
	editing_allowed BOOLEAN,
	valid_from DATETIME,
	valid_until DATETIME,
	evidence_reference VARCHAR(500),
	memo VARCHAR(2000),
	verified_by_user_id INTEGER NOT NULL,
	verified_at DATETIME NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_image_rights_evidence_company_id ON image_rights_evidence (company_id);
CREATE INDEX ix_image_rights_evidence_id ON image_rights_evidence (id);
CREATE INDEX ix_image_rights_evidence_source_domain ON image_rights_evidence (source_domain);
CREATE INDEX ix_image_rights_evidence_supplier_id ON image_rights_evidence (supplier_id);

CREATE TABLE image_rights_acknowledgements (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	asset_id INTEGER NOT NULL,
	workflow_stage VARCHAR(40) NOT NULL,
	warning_code VARCHAR(50) NOT NULL,
	user_action VARCHAR(30) NOT NULL,
	image_fingerprint VARCHAR(64) NOT NULL,
	acknowledged_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_image_rights_acknowledgements_asset_id ON image_rights_acknowledgements (asset_id);
CREATE INDEX ix_image_rights_acknowledgements_company_id ON image_rights_acknowledgements (company_id);
CREATE INDEX ix_image_rights_acknowledgements_id ON image_rights_acknowledgements (id);

COMMIT;

-- Rollback (주석 — 자동 실행 안 됨):
-- BEGIN;
-- DROP INDEX ix_image_rights_acknowledgements_id;
-- DROP INDEX ix_image_rights_acknowledgements_company_id;
-- DROP INDEX ix_image_rights_acknowledgements_asset_id;
-- DROP TABLE image_rights_acknowledgements;
-- DROP INDEX ix_image_rights_evidence_supplier_id;
-- DROP INDEX ix_image_rights_evidence_source_domain;
-- DROP INDEX ix_image_rights_evidence_id;
-- DROP INDEX ix_image_rights_evidence_company_id;
-- DROP TABLE image_rights_evidence;
-- COMMIT;
