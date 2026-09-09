"""
=========================================================
Homez OS

File : tests/test_pricing_csv_export.py

V7 Gate 5(2026-08-15) — 회사별 회계(정산/마진) CSV 내보내기 검증
(요구사항 6/7). `app/domains/marketplace_listing/
listing_wizard_csv_export.py`(Gate U-3)가 이미 확립한 CSV 보안 계약
(Formula Injection 방어/BOM/최대행수/ko-KR·en-US 헤더)을 그대로
재사용했는지 검증한다.
=========================================================
"""

import os
import tempfile
import unittest
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.company.model import Company
from app.domains.marketplace_listing.model import MarketplaceAccount
from app.domains.marketplace_listing.model import MarketplaceChannel
from app.domains.marketplace_listing.model import MarketplaceListing
from app.domains.pricing.csv_export import CSV_UTF8_BOM
from app.domains.pricing.csv_export import _csv_safe
from app.domains.pricing.model import MarginSnapshot
from app.domains.pricing.model import PriceChangeRequest
from app.domains.pricing.model import PriceChangeStatusEvent
from app.domains.pricing.model import ProductPricing
from app.domains.pricing.model import SettlementReconciliation
from app.domains.pricing.schema import ProductPricingInitCreate
from app.domains.pricing.service import PricingService
from app.domains.user.model import User  # noqa: F401 (Company.relationship("User") 해석용)


class PricingCsvExportTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__,
                MarketplaceChannel.__table__,
                MarketplaceAccount.__table__,
                MarketplaceListing.__table__,
                ProductPricing.__table__,
                PriceChangeRequest.__table__,
                PriceChangeStatusEvent.__table__,
                MarginSnapshot.__table__,
                SettlementReconciliation.__table__,
            ],
        )
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        self.company = Company(
            name="회사 A", business_number="111-11-11111",
            ceo="대표A", phone="02-000-0001",
            email="a@example.com", address="서울",
        )
        self.db.add(self.company)
        self.db.commit()
        self.company_id = self.company.id

        channel = MarketplaceChannel(
            code="FAKE", name="가짜채널", doc_verification_status="VERIFIED",
        )
        self.db.add(channel)
        self.db.commit()

        account = MarketplaceAccount(
            company_id=self.company_id, channel_id=channel.id,
            account_code="acc1", account_name="계정1",
        )
        self.db.add(account)
        self.db.commit()

        listing = MarketplaceListing(
            company_id=self.company_id, product_candidate_id=1,
            marketplace_account_id=account.id, status="DRAFT",
        )
        self.db.add(listing)
        self.db.commit()
        self.listing_id = listing.id

        self.service = PricingService(self.db)
        self.service.initialize_pricing(
            self.company_id,
            ProductPricingInitCreate(
                listing_id=self.listing_id,
                initial_sale_price=Decimal("10000"),
                cost_of_goods=Decimal("5000"),
                shipping_cost=Decimal("500"),
                packaging_cost=Decimal("100"),
                ad_cost=Decimal("200"),
                channel_fee_rate=Decimal("0.1"),
                payment_fee_rate=Decimal("0.03"),
                return_reserve_rate=Decimal("0.01"),
                tax_basis_rate=Decimal("0.0"),
            ),
            1,
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_include_economics_true_has_money_columns(self):

        csv_text = self.service.export_accounting_csv(
            self.company_id, include_economics=True,
        )
        self.assertTrue(csv_text.startswith(CSV_UTF8_BOM))
        header_line = csv_text.splitlines()[0]
        self.assertIn("현재 판매가", header_line)
        self.assertIn("예상 마진액", header_line)

    def test_include_economics_false_drops_money_columns_entirely(self):

        csv_text = self.service.export_accounting_csv(
            self.company_id, include_economics=False,
        )
        header_line = csv_text.splitlines()[0]
        self.assertNotIn("현재 판매가", header_line)
        self.assertNotIn("예상 마진액", header_line)
        self.assertIn("리스팅 ID", header_line)

    def test_locale_switch_changes_header_language(self):

        csv_text = self.service.export_accounting_csv(
            self.company_id, include_economics=True, locale="en-US",
        )
        header_line = csv_text.splitlines()[0]
        self.assertIn("Current Sale Price", header_line)
        self.assertIn("Expected Margin Amount", header_line)


class CsvFormulaInjectionTestCase(unittest.TestCase):
    """listing_wizard_csv_export.py와 동일한 방어 계약을 재사용했는지."""

    def test_formula_prefixes_are_neutralized(self):

        for dangerous in ("=SUM(A1)", "+CMD", "-1+1", "@import", "\ttab"):
            safe = _csv_safe(dangerous)
            self.assertTrue(safe.startswith("'"))
            self.assertEqual(safe[1:], dangerous)

    def test_safe_values_are_untouched(self):

        for value in ("정상값", "12000", "MATCHED", None):
            self.assertEqual(_csv_safe(value), "" if value is None else value)


if __name__ == "__main__":
    unittest.main()
