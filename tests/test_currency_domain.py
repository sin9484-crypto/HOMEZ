"""
=========================================================
Homez OS

File : tests/test_currency_domain.py

2026-09-10 Phase 9(HOMEZ_USER_OPERATION_SETTINGS.md 6번) — 환율
기록·조회·변환, 허용률 판정 검증. 실제 외부 환율 API는 전혀 호출
하지 않는다 — 전부 수동 입력값만 다룬다.
=========================================================
"""

import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.database.bootstrap import bootstrap_environment
from app.domains.company.model import Company
from app.domains.currency.constants import DEFAULT_EXCHANGE_RATE_TOLERANCE_PERCENT
from app.domains.currency.service import ExchangeRateNotFoundError
from app.domains.currency.service import ExchangeRateService
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.user.model import User

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"


class CurrencyDomainTestCase(unittest.TestCase):

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
            name="환율테스트 회사", business_number="101-01-01011",
            ceo="테스트", phone="02-000-0000",
            email="currency@example.com", address="테스트",
        )
        self.db.add(self.company)
        self.db.commit()

        self.role = Role(name="Administrator", code="SUPER_ADMIN")
        self.db.add(self.role)
        self.db.commit()

        self.admin = User(
            company_id=self.company.id, username="currencyadmin",
            email="currencyadmin@example.com", password_hash="x",
            name="관리자", role_id=self.role.id, is_active=True,
        )
        self.db.add(self.admin)
        self.db.commit()

        self.service = ExchangeRateService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if self.db_path.exists():
            self.db_path.unlink()

    # ------------------------------
    # 기록·조회
    # ------------------------------

    def test_record_rate_creates_row(self):

        rate = self.service.record_rate(
            base_currency="USD", quote_currency="KRW", rate=1350.0,
            recorded_by=self.admin.id,
        )
        self.assertEqual(rate.rate, 1350.0)
        self.assertEqual(rate.source, "MANUAL_ADMIN_ENTRY")

    def test_record_rate_rejects_non_positive(self):

        with self.assertRaises(BadRequestException):
            self.service.record_rate(
                base_currency="USD", quote_currency="KRW", rate=0,
            )

    def test_record_rate_rejects_same_currency(self):

        with self.assertRaises(BadRequestException):
            self.service.record_rate(
                base_currency="KRW", quote_currency="KRW", rate=1.0,
            )

    def test_get_latest_rate_returns_most_recent(self):

        self.service.record_rate(
            base_currency="USD", quote_currency="KRW", rate=1300.0,
        )
        self.service.record_rate(
            base_currency="USD", quote_currency="KRW", rate=1350.0,
        )

        latest = self.service.get_latest_rate("USD", "KRW")
        self.assertEqual(latest.rate, 1350.0)

    def test_get_latest_rate_none_when_never_recorded(self):

        self.assertIsNone(self.service.get_latest_rate("CNY", "KRW"))

    def test_currency_codes_are_case_insensitive(self):

        self.service.record_rate(
            base_currency="usd", quote_currency="krw", rate=1350.0,
        )
        self.assertIsNotNone(self.service.get_latest_rate("USD", "KRW"))

    # ------------------------------
    # 변환
    # ------------------------------

    def test_convert_same_currency_is_identity(self):

        result = self.service.convert(Decimal("100"), "KRW", "KRW")
        self.assertEqual(result, Decimal("100"))

    def test_convert_uses_latest_rate(self):

        self.service.record_rate(
            base_currency="USD", quote_currency="KRW", rate=1350.0,
        )

        result = self.service.convert(Decimal("10"), "USD", "KRW")
        self.assertEqual(result, Decimal("13500.0"))

    def test_convert_never_guesses_missing_rate(self):

        with self.assertRaises(ExchangeRateNotFoundError):
            self.service.convert(Decimal("10"), "JPY", "KRW")

    # ------------------------------
    # 허용률
    # ------------------------------

    def test_tolerance_defaults_to_document_initial_value(self):

        self.assertEqual(
            self.service.get_tolerance_percent(self.company.id),
            DEFAULT_EXCHANGE_RATE_TOLERANCE_PERCENT,
        )

    def test_set_tolerance_overrides_default(self):

        self.service.set_tolerance_percent(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, tolerance_percent=5.0,
        )
        self.assertEqual(
            self.service.get_tolerance_percent(self.company.id), 5.0,
        )

    def test_set_tolerance_requires_admin(self):

        with self.assertRaises(ForbiddenException):
            self.service.set_tolerance_percent(
                company_id=self.company.id, user_id=self.admin.id,
                is_admin=False, tolerance_percent=5.0,
            )

    def test_set_tolerance_rejects_non_positive(self):

        with self.assertRaises(BadRequestException):
            self.service.set_tolerance_percent(
                company_id=self.company.id, user_id=self.admin.id,
                is_admin=True, tolerance_percent=0,
            )

    def test_tolerance_history_is_append_only_latest_wins(self):

        self.service.set_tolerance_percent(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, tolerance_percent=3.0,
        )
        self.service.set_tolerance_percent(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, tolerance_percent=4.0,
        )
        self.assertEqual(
            self.service.get_tolerance_percent(self.company.id), 4.0,
        )

    # ------------------------------
    # 허용률 판정
    # ------------------------------

    def test_within_default_tolerance_is_allowed(self):

        allowed, reason = self.service.check_rate_within_tolerance(
            self.company.id, rate_at_baseline=1350.0, current_rate=1360.0,
        )
        self.assertTrue(allowed)
        self.assertIsNone(reason)

    def test_beyond_default_tolerance_is_denied(self):

        allowed, reason = self.service.check_rate_within_tolerance(
            self.company.id, rate_at_baseline=1350.0, current_rate=1450.0,
        )
        self.assertFalse(allowed)
        self.assertIsNotNone(reason)

    def test_custom_tolerance_is_honored(self):

        self.service.set_tolerance_percent(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, tolerance_percent=10.0,
        )

        allowed, _ = self.service.check_rate_within_tolerance(
            self.company.id, rate_at_baseline=1000.0, current_rate=1090.0,
        )
        self.assertTrue(allowed)

    def test_tolerance_check_direction_agnostic(self):
        """환율이 내려가는 경우도 상승과 동일하게 판정해야 한다."""

        allowed, _ = self.service.check_rate_within_tolerance(
            self.company.id, rate_at_baseline=1350.0, current_rate=1250.0,
        )
        self.assertFalse(allowed)

    def test_tolerance_check_rejects_non_positive_baseline(self):

        with self.assertRaises(BadRequestException):
            self.service.check_rate_within_tolerance(
                self.company.id, rate_at_baseline=0, current_rate=1350.0,
            )


if __name__ == "__main__":
    unittest.main()
