-- Purpose: suppliers 운영 스키마 드리프트 해소(2026-08-21 CP-1,
-- CTO 지시 — "suppliers Model/Schema 불일치 최우선 해결") — 실제
-- 운영 DB(그리고 개발 DB)의 suppliers 테이블은 2026-08-16 Gate9
-- 핵심 스키마 Migration(20260816_00_create_v7_gate9_core_foundation_
-- schema.sql)이 만든 예전(회사별 비공개 공급처 등록부) 8개 컬럼
-- (id/company_id/name/business_number/ceo/phone/email/address/
-- active)만 갖고 있다. 반면 현재 ORM(app/domains/supplier/model.py
-- ::Supplier)은 2026-08-20 CTO 정정(app/domains/source/model.py
-- 헤더 주석 — "suppliers는 상호·공개 사업자정보·활성/검증여부 같은
-- 공개 식별정보만 전역 공유") 이후의 20개 컬럼짜리 "공개 카탈로그"
-- 모델이다. 이 Migration은 그 갭을 additive ALTER TABLE로만 메운다
-- — 기존 8개 레거시 컬럼은 그대로 둔다(즉시 삭제 불필요, 별도 후속
-- Migration으로 미룸 — 아래 "레거시 컬럼 처리" 참고).
--
-- 실제 운영 DB와 개발 DB 모두 이 시점 suppliers 행 수는 0건으로
-- 직접 확인했다(2026-08-21, read-only PRAGMA/SELECT COUNT(*)) —
-- 즉 지금 이 Migration이 실제로 보존해야 할 기존 데이터는 없다.
-- 다만 이 Migration 자체는 "언젠가 레거시 행이 존재하는 DB에
-- 적용돼도 안전"해야 하므로, 존재할 수 있는 레거시 행을 파괴하거나
-- 추정값으로 덮어쓰지 않는 방식으로 설계한다(아래 각 컬럼 근거 참고)
-- — tests/test_supplier_legacy_schema_upgrade.py가 실제로 레거시
-- 행이 있는 fixture로 이를 검증한다.
--
-- 컬럼별 근거:
--   - code: UNIQUE, nullable. 레거시 행에는 대응값이 없으므로 억지
--     backfill 없이 NULL로 둔다 — SQLite UNIQUE 인덱스는 NULL을
--     서로 다른 값으로 취급하므로 다건이어도 충돌하지 않는다.
--   - supplier_type: NOT NULL DEFAULT 'GENERAL' — ORM 기본값과 동일.
--   - is_active: **레거시 active 컬럼 값을 그대로 복사하지 않는다.**
--     레거시 8컬럼 스키마는 "회사(company_id)가 소유하는 비공개
--     공급처 등록부" 모델이었다 — 그 시절의 active=1은 "그 회사
--     안에서 활성"이라는 뜻이었지, "전역 공개 디렉터리에 노출
--     되어도 좋다"는 뜻이 아니다. 그대로 복사하면
--     GET /sourcing/suppliers/public(모든 회사에 공개)이 과거
--     한 회사의 비공개 공급처 등록 행을 다른 회사에 즉시 노출하는
--     회사 격리 위반이 된다. 그래서 신규 is_active는 레거시 값과
--     무관하게 전부 DEFAULT 0(비공개)으로 시작한다 — 공개 전환은
--     반드시 운영자가 각 행을 검토해 명시적으로 켜야 한다.
--   - is_verified/trust_score/delivery_score/return_rate/
--     order_count/success_count/ai_score: 전부 추정 근거가 없으므로
--     ORM 기본값(0/0.0/False)으로만 시작한다.
--   - description/website/api_url/api_key/api_secret/
--     search_keywords/metadata_json: 전부 nullable, 레거시에 대응
--     값 없음 — NULL.
--     (api_key/api_secret: 현재 코드베이스 전체에서 실제로 읽거나
--     쓰는 도달 가능한 경로가 없다 — app/main.py에 supplier_router는
--     import만 되고 include_router는 주석 처리되어 있어 마운트되지
--     않는다(app/main.py:354). 즉 이 두 컬럼은 지금 어떤 요청에서도
--     실제로 채워지지 않는다. 그럼에도 ORM 모델이 선언한 컬럼인 이상
--     `db.query(Supplier)...` 계열 쿼리 전체가 이 컬럼을 SELECT
--     목록에 포함하므로(실제로 컬럼이 없으면 모든 Supplier 쿼리가
--     "no such column" 500으로 죽는다 — 이번에 재현 확인한 결함
--     자체가 이 사실을 증명한다), Model-DB 정합을 위해 추가는
--     필수다. 다만 "suppliers=공개 전용" 원칙과 상충하는 설계
--     잔재이므로, 이 두 컬럼을 완전히 제거할지는 별도 후속 CTO
--     결정·Migration으로 넘긴다 — 이번 Migration은 정합성 확보만
--     한다.)
--   - created_at/updated_at: **레거시 행의 실제 생성/수정 시각을
--     알 수 없으므로 추정하지 않는다** — 20260821_00_add_audit_logs_
--     created_at.sql과 동일한 원칙으로 DEFAULT 없이 nullable로만
--     추가한다. ORM의 Python 쪽 default=datetime.utcnow는 이
--     Migration 이후 앱이 실제로 만드는 신규 행부터는 정상적으로
--     채운다(SQLAlchemy가 INSERT 시 Python 쪽 기본값을 적용하므로
--     DB 컬럼 자체의 NOT NULL 여부와 무관하게 동작) — NULL로 남는
--     것은 이 Migration 적용 이전에 이미 존재했던 행뿐이다.
--   - deleted_at: 원래부터 nullable — 그대로 nullable.
--
-- 레거시 컬럼(company_id/business_number/ceo/phone/email/address/
-- active) 처리: 이번 Migration은 이 7개 컬럼을 전혀 건드리지 않는다
-- (삭제도, rename도, 값 복사도 없음). ORM이 이 컬럼들을 선언하지
-- 않으므로 SQLAlchemy는 그냥 무시한다(안전, 이 코드베이스 전역에서
-- 이미 쓰이는 패턴). 실제 레거시 행이 생기면(현재는 0건) 그 회사별
-- 비공개 연락처·사업자정보는 이미 존재하는
-- company_supplier_relations(20260820_01, contact_name/
-- contact_phone/contact_email/notes)로 옮기는 것이 올바른 방향이나,
-- 지금 옮길 실제 데이터가 없으므로 이번 Migration 범위에 넣지
-- 않는다(추측 기반 데이터 이관 금지 원칙) — 별도 후속 과제로 문서화.
--
-- 이 Migration은 실제 homez.db(개발·운영 모두)에 적용하지 않는다.
-- 임시 SQLite 파일(신규 fixture + 레거시 fixture 양쪽)에서만
-- 검증했다(tests/test_supplier_legacy_schema_upgrade.py).

BEGIN;

ALTER TABLE suppliers ADD COLUMN code VARCHAR(100);
ALTER TABLE suppliers ADD COLUMN supplier_type VARCHAR(50) NOT NULL DEFAULT 'GENERAL';
ALTER TABLE suppliers ADD COLUMN description TEXT;
ALTER TABLE suppliers ADD COLUMN website VARCHAR(500);
ALTER TABLE suppliers ADD COLUMN api_url VARCHAR(500);
ALTER TABLE suppliers ADD COLUMN api_key VARCHAR(500);
ALTER TABLE suppliers ADD COLUMN api_secret VARCHAR(500);
ALTER TABLE suppliers ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE suppliers ADD COLUMN is_verified BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE suppliers ADD COLUMN trust_score FLOAT NOT NULL DEFAULT 0;
ALTER TABLE suppliers ADD COLUMN delivery_score FLOAT NOT NULL DEFAULT 0;
ALTER TABLE suppliers ADD COLUMN return_rate FLOAT NOT NULL DEFAULT 0;
ALTER TABLE suppliers ADD COLUMN order_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE suppliers ADD COLUMN success_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE suppliers ADD COLUMN ai_score FLOAT NOT NULL DEFAULT 0;
ALTER TABLE suppliers ADD COLUMN search_keywords TEXT;
ALTER TABLE suppliers ADD COLUMN metadata_json TEXT;
ALTER TABLE suppliers ADD COLUMN created_at DATETIME;
ALTER TABLE suppliers ADD COLUMN updated_at DATETIME;
ALTER TABLE suppliers ADD COLUMN deleted_at DATETIME;

CREATE UNIQUE INDEX ix_suppliers_code ON suppliers (code);
CREATE INDEX ix_suppliers_is_active ON suppliers (is_active);
CREATE INDEX ix_suppliers_name ON suppliers (name);

COMMIT;

-- Rollback(주석 전용, 자동 실행되지 않음 — SQLite는 컬럼 DROP에
-- 테이블 재생성이 필요하다, 기존 additive Migration들과 동일한 제약):
-- CREATE TABLE suppliers_old_20260821_01 AS SELECT
--   id, company_id, name, business_number, ceo, phone, email,
--   address, active FROM suppliers;
-- DROP TABLE suppliers;
-- ALTER TABLE suppliers_old_20260821_01 RENAME TO suppliers;
-- (UNIQUE(name), FK(company_id), ix_suppliers_id 재생성 필요)
