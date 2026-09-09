-- Purpose: 판매채널 연결(StoreConnection) — 쿠팡/네이버 공식 API
-- 자격증명 연결 상태 관리.
-- Source of Truth: app/domains/store_connection/model.py
--
-- 대상 1개 테이블 (FK 없음 — company_id/created_by는 논리 참조
-- 컬럼, 이 코드베이스의 다른 모든 Domain과 동일한 컨벤션):
--   store_connections
--
-- Secret(Access Key/Secret Key/Client Secret) 원문은 이 테이블 어디에도
-- 없다 — credential_reference는 Windows Credential Manager의
-- target name일 뿐이다(app/core/windows_credential_store.py).
--
-- 중복 정책: (company_id, marketplace_code, seller_identifier)는
-- UNIQUE다. (company_id, creation_idempotency_key)도 복합 UNIQUE다
-- (2026-08-01 CTO 재심사 이후 정책 변경 — 이전에는
-- creation_idempotency_key 단독 전역 UNIQUE였다). 전역 UNIQUE였을
-- 때는 서로 다른 두 회사가 우연히/의도적으로 같은 idempotency key를
-- 쓰면 IntegrityError 복구 경로가 첫 번째 회사의 행을 두 번째 회사의
-- "idempotent 재호출 결과"로 잘못 반환하는 회사 간 데이터 유출
-- 경로였다. 이 Migration은 2026-08-01 Gate 2에서 실제 homez.db에
-- 적용됐다(backups/homez_pre_store_connection_migration_20260801.db,
-- tests/test_store_connection_migration.py의 읽기전용 재확인 테스트로
-- 계속 고정됨) — 아래 두 문단의 "아직 미적용" 서술은 이 파일을 그
-- 자리에서 계속 고쳐온 이력의 흔적으로, 2026-08-04 V6 Gate 2
-- 재감사에서 실제 상태와 어긋난다는 점이 발견돼 바로잡는다. 새
-- 후속 Migration 파일을 만들지 않고 이 파일을 그 자리에서 수정하는
-- 관행 자체는 그대로 유지한다(model.py가 Source of Truth).
--
-- creation_request_fingerprint(2026-08-01 CTO 2차 재심사 반영,
-- 같은 날 추가 수정)는 같은 idempotency_key 재호출이 "정말 같은
-- 요청"인지 판별하는 64자 hex HMAC-SHA256 다이제스트다(app/domains/
-- store_connection/idempotency_fingerprint.py). Secret 원문이나
-- canonical JSON을 담지 않는다 — 오직 다이제스트만 저장한다. 이전에는
-- 같은 idempotency_key의 기존 행이 있으면 요청 내용 비교 없이 그대로
-- 반환했다(다른 marketplace_code/seller_identifier/display_name/
-- Credential로 같은 key를 재사용해도 오류 없이 엉뚱한 기존 행을
-- 돌려받는 결함). 이 컬럼도 위와 같은 2026-08-01 적용에 포함돼
-- 이미 실제 homez.db에 존재한다 — 새 파일을 만들지 않고 같은
-- 자리에서 컬럼을 추가하는 관행 자체는 유지한다.
--
-- 이 Migration은 이미 적용 완료된 상태다(위 참고). 향후 이 파일을
-- 다시 수정할 일이 있다면, 반드시 임시 DB와 실제 homez.db의 디스크
-- 복사본에서 먼저 리허설하고, 실제 원본 적용은 별도 승인을 받는다.
--
-- 실행 전 필수 확인: store_connections 테이블이 대상 DB에 존재하지
-- 않아야 한다(IF NOT EXISTS를 쓰지 않음 — 부분 적용 상태를 그대로
-- 실패시킨다).

BEGIN;

CREATE TABLE store_connections (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	marketplace_code VARCHAR(30) NOT NULL,
	display_name VARCHAR(100) NOT NULL,
	seller_identifier VARCHAR(100) NOT NULL,
	credential_reference VARCHAR(200),
	masked_credential_hint VARCHAR(50),
	connection_status VARCHAR(20) NOT NULL,
	credential_version INTEGER NOT NULL,
	expires_at DATETIME,
	last_verified_at DATETIME,
	last_success_at DATETIME,
	last_error_code VARCHAR(50),
	last_error_summary VARCHAR(300),
	created_by INTEGER NOT NULL,
	creation_idempotency_key VARCHAR(100) NOT NULL,
	creation_request_fingerprint VARCHAR(64) NOT NULL,
	last_mutation_idempotency_key VARCHAR(100),
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_store_connections_company_marketplace_seller UNIQUE (company_id, marketplace_code, seller_identifier),
	CONSTRAINT uq_store_connections_company_creation_idempotency_key UNIQUE (company_id, creation_idempotency_key)
);

CREATE INDEX ix_store_connections_company_id ON store_connections (company_id);
CREATE INDEX ix_store_connections_id ON store_connections (id);
CREATE INDEX ix_store_connections_marketplace_code ON store_connections (marketplace_code);
CREATE INDEX ix_store_connections_connection_status ON store_connections (connection_status);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로 실행):
-- BEGIN;
-- DROP INDEX ix_store_connections_connection_status;
-- DROP INDEX ix_store_connections_marketplace_code;
-- DROP INDEX ix_store_connections_id;
-- DROP INDEX ix_store_connections_company_id;
-- DROP TABLE store_connections;
-- COMMIT;
