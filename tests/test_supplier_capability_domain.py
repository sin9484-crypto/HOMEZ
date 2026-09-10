"""
=========================================================
Homez OS

File : tests/test_supplier_capability_domain.py

2026-09-10 Phase 9(HOMEZ_USER_OPERATION_SETTINGS.md 7번) — 공급처
프로필·능력 플래그 검증. "미확인은 절대 추측하지 않는다"가 이
파일의 핵심 검증 대상이다.
=========================================================
"""

import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.database.bootstrap import bootstrap_environment
from app.domains.supplier_capability.constants import CapabilitySupport
from app.domains.supplier_capability.constants import SupplierCapabilityFlag
from app.domains.supplier_capability.service import SupplierCapabilityService

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"


class SupplierCapabilityDomainTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        self.db_path = Path(path)
        self.backups_dir = Path(tempfile.mkdtemp())

        result = bootstrap_environment(
            db_path=self.db_path, migrations_dir=MIGRATIONS_DIR,
            backups_dir=self.backups_dir,
        )
        self.assertTrue(result.is_new_install)
        self.assertFalse(result.migration_approval_required)

        self.engine = create_engine(f"sqlite:///{self.db_path}")
        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()

        self.service = SupplierCapabilityService(self.db)
        self.supplier_id = 4242
        self.admin_id = 1

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if self.db_path.exists():
            self.db_path.unlink()

    # ------------------------------
    # 프로필
    # ------------------------------

    def test_get_or_create_profile_defaults_to_domestic_direct_consignment(self):

        profile = self.service.get_or_create_profile(self.supplier_id)

        self.assertFalse(profile.is_international)
        self.assertEqual(profile.default_currency, "KRW")
        self.assertTrue(profile.consignment_direct_to_customer)

    def test_get_or_create_profile_is_idempotent(self):

        first = self.service.get_or_create_profile(self.supplier_id)
        second = self.service.get_or_create_profile(self.supplier_id)

        self.assertEqual(first.id, second.id)

    def test_set_profile_marks_international_supplier(self):

        profile = self.service.set_profile(
            supplier_id=self.supplier_id, user_id=self.admin_id,
            is_admin=True, is_international=True, country_code="US",
            default_currency="USD",
        )

        self.assertTrue(profile.is_international)
        self.assertEqual(profile.country_code, "US")
        self.assertEqual(profile.default_currency, "USD")

    def test_set_profile_requires_admin(self):

        with self.assertRaises(ForbiddenException):
            self.service.set_profile(
                supplier_id=self.supplier_id, user_id=self.admin_id,
                is_admin=False, is_international=True,
            )

    def test_set_profile_can_override_default_consignment_flow(self):

        profile = self.service.set_profile(
            supplier_id=self.supplier_id, user_id=self.admin_id,
            is_admin=True, consignment_direct_to_customer=False,
        )
        self.assertFalse(profile.consignment_direct_to_customer)

    def test_set_profile_partial_update_preserves_other_fields(self):

        self.service.set_profile(
            supplier_id=self.supplier_id, user_id=self.admin_id,
            is_admin=True, is_international=True, country_code="CN",
        )
        updated = self.service.set_profile(
            supplier_id=self.supplier_id, user_id=self.admin_id,
            is_admin=True, default_currency="CNY",
        )

        self.assertTrue(updated.is_international)
        self.assertEqual(updated.country_code, "CN")
        self.assertEqual(updated.default_currency, "CNY")

    # ------------------------------
    # 능력 플래그 — "미확인은 절대 추측하지 않는다"
    # ------------------------------

    def test_unrecorded_supplier_shows_all_eight_flags_as_unknown(self):

        matrix = self.service.get_capability_matrix(self.supplier_id)

        self.assertEqual(len(matrix), 8)
        for flag in SupplierCapabilityFlag.ALL:
            self.assertIn(flag, matrix)
            self.assertEqual(matrix[flag]["support"], CapabilitySupport.UNKNOWN)

    def test_set_capability_updates_only_that_flag(self):

        self.service.set_capability(
            supplier_id=self.supplier_id, user_id=self.admin_id,
            is_admin=True, capability=SupplierCapabilityFlag.STOCK_INFO,
            support=CapabilitySupport.SUPPORTED,
            note="실시간 재고 API 확인함",
        )

        matrix = self.service.get_capability_matrix(self.supplier_id)

        self.assertEqual(
            matrix[SupplierCapabilityFlag.STOCK_INFO]["support"],
            CapabilitySupport.SUPPORTED,
        )
        # 나머지 7개는 여전히 UNKNOWN이어야 한다(추측 확산 금지).
        for flag in SupplierCapabilityFlag.ALL:
            if flag == SupplierCapabilityFlag.STOCK_INFO:
                continue
            self.assertEqual(matrix[flag]["support"], CapabilitySupport.UNKNOWN)

    def test_set_capability_upserts_not_duplicates(self):

        self.service.set_capability(
            supplier_id=self.supplier_id, user_id=self.admin_id,
            is_admin=True, capability=SupplierCapabilityFlag.CANCELABILITY,
            support=CapabilitySupport.NOT_SUPPORTED,
        )
        self.service.set_capability(
            supplier_id=self.supplier_id, user_id=self.admin_id,
            is_admin=True, capability=SupplierCapabilityFlag.CANCELABILITY,
            support=CapabilitySupport.SUPPORTED,
        )

        records = self.service.repository.list_records_for_supplier(
            self.supplier_id,
        )
        cancelability_records = [
            r for r in records
            if r.capability == SupplierCapabilityFlag.CANCELABILITY
        ]
        self.assertEqual(len(cancelability_records), 1)
        self.assertEqual(
            cancelability_records[0].support, CapabilitySupport.SUPPORTED,
        )

    def test_set_capability_requires_admin(self):

        with self.assertRaises(ForbiddenException):
            self.service.set_capability(
                supplier_id=self.supplier_id, user_id=self.admin_id,
                is_admin=False,
                capability=SupplierCapabilityFlag.PRICE_INFO,
                support=CapabilitySupport.SUPPORTED,
            )

    def test_set_capability_rejects_unknown_flag(self):

        with self.assertRaises(BadRequestException):
            self.service.set_capability(
                supplier_id=self.supplier_id, user_id=self.admin_id,
                is_admin=True, capability="WARP_SPEED_DELIVERY",
                support=CapabilitySupport.SUPPORTED,
            )

    def test_set_capability_rejects_unknown_support_value(self):

        with self.assertRaises(BadRequestException):
            self.service.set_capability(
                supplier_id=self.supplier_id, user_id=self.admin_id,
                is_admin=True, capability=SupplierCapabilityFlag.PRICE_INFO,
                support="PROBABLY",
            )

    def test_capabilities_are_isolated_per_supplier(self):

        self.service.set_capability(
            supplier_id=self.supplier_id, user_id=self.admin_id,
            is_admin=True, capability=SupplierCapabilityFlag.ORDER_PLACEMENT,
            support=CapabilitySupport.SUPPORTED,
        )

        other_matrix = self.service.get_capability_matrix(9999)
        self.assertEqual(
            other_matrix[SupplierCapabilityFlag.ORDER_PLACEMENT]["support"],
            CapabilitySupport.UNKNOWN,
        )


if __name__ == "__main__":
    unittest.main()
