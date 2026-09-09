"""
=========================================================
Homez OS

File : tests/test_source_service.py

V7 Section F(2026-08-20) — SourceService(공급처 검색·상품 연결)
회사 격리, idempotency, 공급처 활성 검증, 상품 후보 가시성 검증.
=========================================================
"""

import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.source.model import SupplierProductLink
from app.domains.source.schema import SupplierProductLinkCreate
from app.domains.source.service import SourceService
from app.domains.inventory.model import Inventory  # noqa: F401 (Supplier.relationship 해석용)
from app.domains.product.model import Product  # noqa: F401 (Supplier.relationship 해석용)
from app.domains.supplier.model import Supplier
from app.domains.user.model import User  # noqa: F401


class SourceServiceTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__,
                ProductCandidate.__table__,
                ProductCandidateSelection.__table__,
                Supplier.__table__,
                SupplierProductLink.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()
        self.service = SourceService(self.db)

        self.company_a = self._seed_company("A", "111-11-11111")
        self.company_b = self._seed_company("B", "222-22-22222")
        self.supplier = self._seed_supplier(active=True)
        self.inactive_supplier = self._seed_supplier(active=False)
        self.candidate = self._seed_candidate(self.company_a.id)

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

    def _seed_supplier(self, active: bool):

        supplier = Supplier(
            name="테스트 공급처", is_active=active,
        )
        self.db.add(supplier)
        self.db.commit()

        return supplier

    def _seed_candidate(self, owner_company_id):

        candidate = ProductCandidate(
            candidate_key=f"MANUAL:owner:{owner_company_id}:1",
            source_type="MANUAL",
            market="COUPANG",
            source_reference="src-1",
            product_name="샤프란 테스트 상품",
            status=CandidateStatus.APPROVED,
            visibility="PRIVATE",
            owner_company_id=owner_company_id,
        )
        self.db.add(candidate)
        self.db.commit()

        return candidate

    def _make_create(self, **overrides):

        data = {
            "product_candidate_id": self.candidate.id,
            "supplier_id": self.supplier.id,
            "supplier_sku": "SKU-001",
            "unit_cost": 5000.0,
            "moq": 4,
            "lead_time_days": 3,
        }
        data.update(overrides)

        return SupplierProductLinkCreate(**data)

    def test_create_link_success(self):

        link = self.service.create_link(
            self._make_create(), self.company_a.id, created_by=1,
        )

        self.assertEqual(link.company_id, self.company_a.id)
        self.assertEqual(link.supplier_id, self.supplier.id)
        self.assertEqual(link.status, "ACTIVE")

    def test_create_link_idempotent_same_natural_key(self):

        first = self.service.create_link(
            self._make_create(), self.company_a.id, created_by=1,
        )
        second = self.service.create_link(
            self._make_create(unit_cost=9999.0), self.company_a.id, created_by=1,
        )

        self.assertEqual(first.id, second.id)
        # 두 번째 호출의 값으로 덮어쓰지 않는다(첫 생성 값 유지).
        self.assertEqual(second.unit_cost, 5000.0)

    def test_create_link_rejects_inactive_supplier(self):

        with self.assertRaises(BadRequestException):
            self.service.create_link(
                self._make_create(supplier_id=self.inactive_supplier.id),
                self.company_a.id, created_by=1,
            )

    def test_create_link_rejects_unknown_supplier(self):

        with self.assertRaises(BadRequestException):
            self.service.create_link(
                self._make_create(supplier_id=999999),
                self.company_a.id, created_by=1,
            )

    def test_create_link_blocks_candidate_not_visible_to_company(self):
        # candidate는 company_a 소유 PRIVATE — company_b는 볼 수 없다.

        with self.assertRaises(NotFoundException):
            self.service.create_link(
                self._make_create(), self.company_b.id, created_by=1,
            )

    def test_list_links_sorted_by_cost_and_company_scoped(self):

        self.service.create_link(
            self._make_create(supplier_sku="SKU-CHEAP", unit_cost=1000.0),
            self.company_a.id, created_by=1,
        )
        self.service.create_link(
            self._make_create(supplier_sku="SKU-EXPENSIVE", unit_cost=9000.0),
            self.company_a.id, created_by=1,
        )

        links = self.service.list_links_for_candidate(
            self.candidate.id, self.company_a.id,
        )

        self.assertEqual(len(links), 2)
        self.assertEqual(links[0].supplier_sku, "SKU-CHEAP")
        self.assertEqual(links[1].supplier_sku, "SKU-EXPENSIVE")

    def test_get_best_link_returns_cheapest(self):

        self.service.create_link(
            self._make_create(supplier_sku="SKU-CHEAP", unit_cost=1000.0),
            self.company_a.id, created_by=1,
        )
        self.service.create_link(
            self._make_create(supplier_sku="SKU-EXPENSIVE", unit_cost=9000.0),
            self.company_a.id, created_by=1,
        )

        best = self.service.get_best_link(self.candidate.id, self.company_a.id)

        self.assertEqual(best.supplier_sku, "SKU-CHEAP")

    def test_get_best_link_none_when_no_links(self):

        best = self.service.get_best_link(self.candidate.id, self.company_a.id)

        self.assertIsNone(best)

    def test_deactivate_link_excludes_from_list(self):

        link = self.service.create_link(
            self._make_create(), self.company_a.id, created_by=1,
        )
        self.service.deactivate_link(link.id, self.company_a.id)

        links = self.service.list_links_for_candidate(
            self.candidate.id, self.company_a.id,
        )
        self.assertEqual(links, [])

    def test_deactivate_link_blocks_cross_company(self):

        link = self.service.create_link(
            self._make_create(), self.company_a.id, created_by=1,
        )

        with self.assertRaises(NotFoundException):
            self.service.deactivate_link(link.id, self.company_b.id)

    def test_deactivate_already_inactive_conflicts(self):

        link = self.service.create_link(
            self._make_create(), self.company_a.id, created_by=1,
        )
        self.service.deactivate_link(link.id, self.company_a.id)

        from app.core.exceptions import ConflictException

        with self.assertRaises(ConflictException):
            self.service.deactivate_link(link.id, self.company_a.id)


if __name__ == "__main__":
    unittest.main()
