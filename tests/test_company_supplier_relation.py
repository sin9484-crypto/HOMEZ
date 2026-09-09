"""
=========================================================
Homez OS

File : tests/test_company_supplier_relation.py

CompanySupplierRelation(2026-08-20 CTO 정정 — Supplier는 공개
식별정보만 전역, 계약·연락처·Credential 참조는 company_id 소유)
회사 격리·승인 흐름 검증.
=========================================================
"""

import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.source.model import CompanySupplierRelation
from app.domains.source.model import SupplierProductLink
from app.domains.source.schema import CompanySupplierRelationCreate
from app.domains.source.service import SourceService
from app.domains.supplier.model import Supplier
from app.domains.user.model import User  # noqa: F401


class CompanySupplierRelationTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__,
                Supplier.__table__,
                SupplierProductLink.__table__,
                CompanySupplierRelation.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()
        self.service = SourceService(self.db)

        self.company_a = self._seed_company("A", "111-11-11111")
        self.company_b = self._seed_company("B", "222-22-22222")
        self.supplier = Supplier(name="테스트 공급처", is_active=True)
        self.db.add(self.supplier)
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _seed_company(self, label, business_number):

        company = Company(
            name=f"회사 {label}", business_number=business_number,
            ceo="테스트", phone="02-000-0000",
            email=f"{label.lower()}@test.com", address="서울",
        )
        self.db.add(company)
        self.db.commit()

        return company

    def test_create_relation_defaults_to_pending(self):

        relation = self.service.create_relation(
            CompanySupplierRelationCreate(
                supplier_id=self.supplier.id, contact_name="김담당",
                payment_terms="월말 정산", credential_reference="cred-ref-1",
            ),
            self.company_a.id, created_by=1,
        )

        self.assertEqual(relation.approval_status, "PENDING")
        self.assertEqual(relation.contact_name, "김담당")

    def test_create_relation_idempotent_per_company_supplier(self):

        first = self.service.create_relation(
            CompanySupplierRelationCreate(supplier_id=self.supplier.id),
            self.company_a.id, created_by=1,
        )
        second = self.service.create_relation(
            CompanySupplierRelationCreate(
                supplier_id=self.supplier.id, contact_name="다른값",
            ),
            self.company_a.id, created_by=1,
        )

        self.assertEqual(first.id, second.id)

    def test_relations_are_company_isolated(self):

        self.service.create_relation(
            CompanySupplierRelationCreate(supplier_id=self.supplier.id),
            self.company_a.id, created_by=1,
        )

        relations_a = self.service.list_relations(self.company_a.id)
        relations_b = self.service.list_relations(self.company_b.id)

        self.assertEqual(len(relations_a), 1)
        self.assertEqual(relations_b, [])

    def test_approve_relation(self):

        relation = self.service.create_relation(
            CompanySupplierRelationCreate(supplier_id=self.supplier.id),
            self.company_a.id, created_by=1,
        )

        approved = self.service.set_relation_approval(
            relation.id, self.company_a.id, approve=True,
        )
        self.assertEqual(approved.approval_status, "APPROVED")

    def test_reject_relation(self):

        relation = self.service.create_relation(
            CompanySupplierRelationCreate(supplier_id=self.supplier.id),
            self.company_a.id, created_by=1,
        )

        rejected = self.service.set_relation_approval(
            relation.id, self.company_a.id, approve=False,
        )
        self.assertEqual(rejected.approval_status, "REJECTED")

    def test_approval_blocks_cross_company(self):

        relation = self.service.create_relation(
            CompanySupplierRelationCreate(supplier_id=self.supplier.id),
            self.company_a.id, created_by=1,
        )

        with self.assertRaises(NotFoundException):
            self.service.set_relation_approval(
                relation.id, self.company_b.id, approve=True,
            )

    def test_response_schema_excludes_credential_secrets_field_names(self):
        # SupplierPublicDirectoryResponse는 api_key/api_secret을 절대
        # 포함하지 않는다(스키마 자체가 그 필드를 선언하지 않음으로
        # 강제).

        from app.domains.source.schema import SupplierPublicDirectoryResponse

        field_names = set(SupplierPublicDirectoryResponse.model_fields.keys())
        self.assertNotIn("api_key", field_names)
        self.assertNotIn("api_secret", field_names)
        self.assertNotIn("metadata_json", field_names)


if __name__ == "__main__":
    unittest.main()
