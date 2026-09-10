"""
=========================================================
Homez OS

File : tests/test_price_stock_safety_domain.py

2026-09-10 Phase 10(HOMEZ_USER_OPERATION_SETTINGS.md 2·7번) — 가상재고
임계값 게이트, 가격 검토주기 설정 검증.
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
from app.domains.company.model import Company
from app.domains.price_stock_safety.constants import DEFAULT_PRICE_REVIEW_CYCLE_DAYS
from app.domains.price_stock_safety.service import PriceStockSafetyService
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.user.model import User

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"


class PriceStockSafetyDomainTestCase(unittest.TestCase):

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

        self.company = Company(
            name="가격재고안전 테스트 회사", business_number="121-21-21212",
            ceo="테스트", phone="02-000-0000",
            email="pss@example.com", address="테스트",
        )
        self.db.add(self.company)
        self.db.commit()

        self.role = Role(name="Administrator", code="SUPER_ADMIN")
        self.db.add(self.role)
        self.db.commit()

        self.admin = User(
            company_id=self.company.id, username="pssadmin",
            email="pssadmin@example.com", password_hash="x",
            name="관리자", role_id=self.role.id, is_active=True,
        )
        self.db.add(self.admin)
        self.db.commit()

        self.service = PriceStockSafetyService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if self.db_path.exists():
            self.db_path.unlink()

    # ------------------------------
    # 가상재고 임계값
    # ------------------------------

    def test_threshold_defaults_to_none_unset(self):

        self.assertIsNone(
            self.service.get_virtual_stock_threshold(self.company.id),
        )

    def test_set_threshold_updates_value(self):

        self.service.set_virtual_stock_threshold(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, threshold_quantity=5,
        )
        self.assertEqual(
            self.service.get_virtual_stock_threshold(self.company.id), 5,
        )

    def test_set_threshold_requires_admin(self):

        with self.assertRaises(ForbiddenException):
            self.service.set_virtual_stock_threshold(
                company_id=self.company.id, user_id=self.admin.id,
                is_admin=False, threshold_quantity=5,
            )

    def test_set_threshold_rejects_negative(self):

        with self.assertRaises(BadRequestException):
            self.service.set_virtual_stock_threshold(
                company_id=self.company.id, user_id=self.admin.id,
                is_admin=True, threshold_quantity=-1,
            )

    def test_threshold_history_is_append_only_latest_wins(self):

        self.service.set_virtual_stock_threshold(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, threshold_quantity=5,
        )
        self.service.set_virtual_stock_threshold(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, threshold_quantity=10,
        )
        self.assertEqual(
            self.service.get_virtual_stock_threshold(self.company.id), 10,
        )

    # ------------------------------
    # 가상재고 게이트 판정
    # ------------------------------

    def test_unset_threshold_always_allows(self):

        allowed, reason = self.service.check_virtual_stock_allows_auto_order(
            self.company.id, 0,
        )
        self.assertTrue(allowed)
        self.assertIsNone(reason)

    def test_stock_above_threshold_is_allowed(self):

        self.service.set_virtual_stock_threshold(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, threshold_quantity=5,
        )
        allowed, _ = self.service.check_virtual_stock_allows_auto_order(
            self.company.id, 10,
        )
        self.assertTrue(allowed)

    def test_stock_at_or_below_threshold_is_denied(self):

        self.service.set_virtual_stock_threshold(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, threshold_quantity=5,
        )
        allowed, reason = self.service.check_virtual_stock_allows_auto_order(
            self.company.id, 5,
        )
        self.assertFalse(allowed)
        self.assertIsNotNone(reason)

        allowed, _ = self.service.check_virtual_stock_allows_auto_order(
            self.company.id, 3,
        )
        self.assertFalse(allowed)

    def test_negative_displayed_stock_rejected(self):

        with self.assertRaises(BadRequestException):
            self.service.check_virtual_stock_allows_auto_order(
                self.company.id, -1,
            )

    def test_thresholds_are_isolated_per_company(self):

        other_company = Company(
            name="다른회사PSS", business_number="131-31-31313",
            ceo="테스트", phone="02-000-0000",
            email="other-pss@example.com", address="테스트",
        )
        self.db.add(other_company)
        self.db.commit()

        self.service.set_virtual_stock_threshold(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, threshold_quantity=100,
        )

        self.assertIsNone(
            self.service.get_virtual_stock_threshold(other_company.id),
        )

    # ------------------------------
    # 가격 검토주기
    # ------------------------------

    def test_review_cycle_defaults_to_document_initial_value(self):

        self.assertEqual(
            self.service.get_review_cycle_days(self.company.id),
            DEFAULT_PRICE_REVIEW_CYCLE_DAYS,
        )
        self.assertEqual(DEFAULT_PRICE_REVIEW_CYCLE_DAYS, 7)

    def test_set_review_cycle_overrides_default(self):

        self.service.set_review_cycle_days(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, review_cycle_days=14,
        )
        self.assertEqual(
            self.service.get_review_cycle_days(self.company.id), 14,
        )

    def test_set_review_cycle_requires_admin(self):

        with self.assertRaises(ForbiddenException):
            self.service.set_review_cycle_days(
                company_id=self.company.id, user_id=self.admin.id,
                is_admin=False, review_cycle_days=14,
            )

    def test_set_review_cycle_rejects_non_positive(self):

        with self.assertRaises(BadRequestException):
            self.service.set_review_cycle_days(
                company_id=self.company.id, user_id=self.admin.id,
                is_admin=True, review_cycle_days=0,
            )


if __name__ == "__main__":
    unittest.main()
