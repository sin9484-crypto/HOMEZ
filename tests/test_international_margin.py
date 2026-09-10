"""
=========================================================
Homez OS

File : tests/test_international_margin.py

2026-09-10 Phase 9(HOMEZ_USER_OPERATION_SETTINGS.md 4·6·7번) — 해외
매입원가 KRW 환산이 기존(변경하지 않은) margin_calculator.py의
calculate_margin()에 그대로 먹히는지 검증한다.
=========================================================
"""

import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.bootstrap import bootstrap_environment
from app.domains.currency.service import ExchangeRateService
from app.domains.purchase_task.international_margin import (
    InternationalCostConversionError,
)
from app.domains.purchase_task.international_margin import InternationalCostInput
from app.domains.purchase_task.international_margin import (
    convert_international_cost_to_krw,
)
from app.domains.purchase_task.margin_calculator import calculate_margin

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"


class InternationalMarginTestCase(unittest.TestCase):

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

        self.engine = create_engine(f"sqlite:///{self.db_path}")
        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()

        self.exchange = ExchangeRateService(self.db)
        self.exchange.record_rate(
            base_currency="USD", quote_currency="KRW", rate=1300.0,
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if self.db_path.exists():
            self.db_path.unlink()

    def test_converts_foreign_price_and_shipping_to_krw(self):

        cost = InternationalCostInput(
            candidate_id=1, foreign_price=Decimal("10"),
            foreign_shipping_fee=Decimal("2"), foreign_currency="USD",
            customs_duty_rate=Decimal("0"),
        )

        converted = convert_international_cost_to_krw(
            cost, exchange_rate_service=self.exchange,
        )

        self.assertEqual(converted.estimated_price, Decimal("13000.0"))
        self.assertEqual(converted.estimated_shipping_fee, Decimal("2600.0"))

    def test_customs_duty_added_into_confirmed_additional_cost(self):

        cost = InternationalCostInput(
            candidate_id=1, foreign_price=Decimal("10"),
            foreign_shipping_fee=Decimal("0"), foreign_currency="USD",
            customs_duty_rate=Decimal("0.08"),
        )

        converted = convert_international_cost_to_krw(
            cost, exchange_rate_service=self.exchange,
        )

        # 13000 * 0.08 = 1040
        self.assertEqual(converted.confirmed_additional_cost, Decimal("1040.0"))

    def test_shipping_days_carried_through(self):

        cost = InternationalCostInput(
            candidate_id=1, foreign_price=Decimal("10"),
            foreign_shipping_fee=Decimal("0"), foreign_currency="USD",
            customs_duty_rate=Decimal("0"),
            international_shipping_days=21,
        )

        converted = convert_international_cost_to_krw(
            cost, exchange_rate_service=self.exchange,
        )

        self.assertEqual(converted.estimated_delivery_days, 21)

    def test_missing_customs_rate_is_never_guessed_as_zero(self):

        cost = InternationalCostInput(
            candidate_id=1, foreign_price=Decimal("10"),
            foreign_shipping_fee=Decimal("0"), foreign_currency="USD",
            customs_duty_rate=None,
        )

        with self.assertRaises(InternationalCostConversionError):
            convert_international_cost_to_krw(
                cost, exchange_rate_service=self.exchange,
            )

    def test_explicit_zero_customs_rate_is_honored(self):

        cost = InternationalCostInput(
            candidate_id=1, foreign_price=Decimal("10"),
            foreign_shipping_fee=Decimal("0"), foreign_currency="USD",
            customs_duty_rate=Decimal("0"),
        )

        converted = convert_international_cost_to_krw(
            cost, exchange_rate_service=self.exchange,
        )
        self.assertEqual(converted.confirmed_additional_cost, Decimal("0"))

    def test_missing_exchange_rate_never_guessed(self):

        cost = InternationalCostInput(
            candidate_id=1, foreign_price=Decimal("10"),
            foreign_shipping_fee=Decimal("0"), foreign_currency="JPY",
            customs_duty_rate=Decimal("0"),
        )

        with self.assertRaises(InternationalCostConversionError):
            convert_international_cost_to_krw(
                cost, exchange_rate_service=self.exchange,
            )

    def test_converted_result_feeds_directly_into_unchanged_calculate_margin(self):
        """이 테스트가 핵심 검증 대상이다 — margin_calculator.py를
        전혀 고치지 않고도 해외 원가가 그대로 먹혀야 한다."""

        cost = InternationalCostInput(
            candidate_id=42, foreign_price=Decimal("20"),
            foreign_shipping_fee=Decimal("5"), foreign_currency="USD",
            customs_duty_rate=Decimal("0.08"),
        )

        converted = convert_international_cost_to_krw(
            cost, exchange_rate_service=self.exchange,
        )

        result = calculate_margin(
            converted,
            coupang_sale_amount=Decimal("50000"),
            coupang_fee_amount=Decimal("3000"),
        )

        self.assertEqual(result.candidate_id, 42)
        self.assertGreater(result.actual_purchase_cost, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
