-- Purpose: V7 Section F 정정(2026-08-20) — 사용자가 "Supplier를
-- 회사 전체가 공유하는 전역 카탈로그로 쓴다"고 확정한 적이 없음을
-- 지적. 공개 식별정보(상호·공개 사업자정보·활성/검증여부)만 전역
-- suppliers 테이블에 남기고, 계약상태·담당자·비공개 연락처·결제조건·
-- Credential 참조·메모처럼 회사마다 달라야 하는 정보는 이 파일이
-- 새로 만드는 company_supplier_relations에 company_id로 격리한다.
-- Source of Truth: app/domains/source/model.py::CompanySupplierRelation
--
-- credential_reference는 실제 자격증명 원문이 아니라 다른 저장소를
-- 가리키는 불투명 참조 문자열만 저장한다(이 코드베이스 전체 원칙).
--
-- 이 Migration은 실제 homez.db에 적용하지 않는다. 임시 SQLite 파일
-- 에서만 검증했다(tests/test_source_supplier_product_links_migration.py
-- 확장).

BEGIN;

CREATE TABLE company_supplier_relations (
	id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	supplier_id INTEGER NOT NULL,
	approval_status VARCHAR(20) NOT NULL,
	contact_name VARCHAR(100),
	contact_phone VARCHAR(50),
	contact_email VARCHAR(200),
	payment_terms VARCHAR(500),
	credential_reference VARCHAR(300),
	notes VARCHAR(1000),
	created_by INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_company_supplier_relations_company_supplier
		UNIQUE (company_id, supplier_id)
);

CREATE INDEX ix_company_supplier_relations_id ON company_supplier_relations (id);
CREATE INDEX ix_company_supplier_relations_company_id ON company_supplier_relations (company_id);
CREATE INDEX ix_company_supplier_relations_supplier_id ON company_supplier_relations (supplier_id);
CREATE INDEX ix_company_supplier_relations_approval_status ON company_supplier_relations (approval_status);

COMMIT;

-- Rollback(주석 전용, 자동 실행되지 않음):
-- DROP TABLE company_supplier_relations;
