"""
=========================================================
Homez OS

File : tests/test_coupang_product_contract.py

HOMEZ V3.1 Coupang Marketplace Integration Foundation
상품 계약 검증: 필수 필드, 구조화된 옵션, Marketplace/RocketGrowth 분리,
GTIN/MPN 예외 근거, 재고 신뢰도 판정, 초안 생성 멱등성.
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.coupang.constants import IntegrationStatus
from app.domains.coupang.constants import PolicyMatchField
from app.domains.coupang.constants import PolicySetStatus
from app.domains.coupang.constants import RiskLevel
from app.domains.coupang.constants import SalesMethod
from app.domains.coupang.model import CoupangDryRunAttempt
from app.domains.coupang.model import CoupangIntegrationDecision
from app.domains.coupang.model import CoupangMarketplaceProduct
from app.domains.coupang.model import CoupangPolicyRule
from app.domains.coupang.model import CoupangPolicySet
from app.domains.coupang.model import CoupangProductNotice
from app.domains.coupang.model import CoupangProductOption
from app.domains.coupang.model import CoupangProfitEstimate
from app.domains.coupang.schema import CoupangDraftCreateRequest
from app.domains.coupang.schema import CoupangProductOptionInput
from app.domains.coupang.service import CoupangIntegrationService
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection



COMPANY_ID = 1
class CoupangProductContractTestCase(unittest.TestCase):
    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                ProductCandidate.__table__,
                ProductCandidateSelection.__table__,
                CoupangMarketplaceProduct.__table__,
                CoupangProductOption.__table__,
                CoupangPolicySet.__table__,
                CoupangPolicyRule.__table__,
                CoupangProductNotice.__table__,
                CoupangProfitEstimate.__table__,
                CoupangDryRunAttempt.__table__,
                CoupangIntegrationDecision.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()
        self.service = CoupangIntegrationService(self.db)

    def _seed_baseline_verified_sellable_policy(self):
        """
        CAT-001 카테고리를 명시적으로 SELLABLE로 확정하는 VERIFIED/
        활성/완전/유효기간 내 정책 세트를 만든다 — 재감사 반영 이후
        정책 검증은 fail-closed이므로, READY_FOR_REVIEW까지 도달해야
        하는 테스트는 이 시드가 필요하다.
        """

        policy_set = CoupangPolicySet(
            policy_set_id=f"test-verified-{id(self)}",
            policy_version="test-v1",
            source_reference="test-source",
            status=PolicySetStatus.VERIFIED,
            is_complete=True,
            is_active=True,
            checked_at=datetime.utcnow() - timedelta(days=1),
            effective_at=datetime.utcnow() - timedelta(days=1),
            expires_at=None,
        )
        self.db.add(policy_set)
        self.db.commit()

        rule = CoupangPolicyRule(
            policy_set_id=policy_set.id,
            match_field=PolicyMatchField.DISPLAY_CATEGORY_CODE,
            match_value="cat-001",
            risk_level=RiskLevel.SELLABLE,
            reason="테스트 기본 판매 가능 카테고리",
        )
        self.db.add(rule)
        self.db.commit()

        return policy_set

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _approved_candidate(self, ref="CAND-1") -> ProductCandidate:

        candidate = ProductCandidate(
            candidate_key=f"COUPANG_API:COUPANG:{ref}",
            source_type="COUPANG_API",
            market="COUPANG",
            source_reference=ref,
            product_name="테스트 상품",
            status=CandidateStatus.APPROVED,
        )
        self.db.add(candidate)
        self.db.commit()

        return candidate

    _UNSET = object()

    def _draft_request(
        self,
        candidate_id: int,
        idempotency_key: str = "idem-1",
        sales_method: str = SalesMethod.MARKETPLACE,
        gtin: str | None = "8801234567890",
        mpn: str | None = None,
        identifier_exemption_reason: str | None = None,
        supplier_stock: int | None = 100,
        safety_stock: int = 10,
        last_stock_checked_at=_UNSET,
    ) -> CoupangDraftCreateRequest:

        if last_stock_checked_at is self._UNSET:
            last_stock_checked_at = datetime.utcnow()

        return CoupangDraftCreateRequest(
            product_candidate_id=candidate_id,
            sales_method=sales_method,
            external_vendor_sku=f"SKU-{candidate_id}",
            seller_product_name="테스트 상품",
            idempotency_key=idempotency_key,
            brand="HOMEZ",
            display_category_code="CAT-001",
            gtin=gtin,
            mpn=mpn,
            identifier_exemption_reason=identifier_exemption_reason,
            sale_price=Decimal("29900"),
            shipping_method="COURIER",
            shipping_company_code="CJ",
            outbound_shipping_place_code="OUT-1",
            return_center_code="RET-1",
            supplier_stock=supplier_stock,
            safety_stock=safety_stock,
            last_stock_checked_at=last_stock_checked_at,
            options=[
                CoupangProductOptionInput(
                    option_name="색상",
                    option_value="블랙",
                    vendor_sku=f"SKU-{candidate_id}-BLACK",
                    price=Decimal("29900"),
                    stock=50,
                ),
            ],
        )

    # --------------------------------------------------
    # ProductCandidate 게이트
    # --------------------------------------------------

    def test_create_draft_requires_approved_candidate(self):

        candidate = ProductCandidate(
            candidate_key="COUPANG_API:COUPANG:NOT-APPROVED",
            source_type="COUPANG_API",
            market="COUPANG",
            source_reference="NOT-APPROVED",
            product_name="미승인 상품",
            status=CandidateStatus.DISCOVERED,
        )
        self.db.add(candidate)
        self.db.commit()

        with self.assertRaises(BadRequestException):
            self.service.create_draft(
                self._draft_request(candidate.id), company_id=COMPANY_ID,
                correlation_id="c1",
            )

    def test_create_draft_missing_candidate_raises_not_found(self):

        with self.assertRaises(NotFoundException):
            self.service.create_draft(
                self._draft_request(999999), company_id=COMPANY_ID,
                correlation_id="c1",
            )

    def test_create_draft_succeeds_for_approved_candidate(self):

        candidate = self._approved_candidate()

        product, duplicate, _events = self.service.create_draft(
            self._draft_request(candidate.id), company_id=COMPANY_ID,
                correlation_id="c1",
        )

        self.assertFalse(duplicate)
        self.assertEqual(product.status, IntegrationStatus.DRAFT)
        self.assertEqual(product.marketplace, "COUPANG")
        self.assertEqual(product.product_candidate_id, candidate.id)

    # --------------------------------------------------
    # 멱등성
    # --------------------------------------------------

    def test_create_draft_is_idempotent(self):

        candidate = self._approved_candidate()
        request = self._draft_request(candidate.id, idempotency_key="idem-x")

        first, first_dup, _ = self.service.create_draft(
            request, company_id=COMPANY_ID,
                correlation_id="c1",
        )
        second, second_dup, _ = self.service.create_draft(
            request, company_id=COMPANY_ID,
                correlation_id="c2",
        )

        self.assertFalse(first_dup)
        self.assertTrue(second_dup)
        self.assertEqual(first.id, second.id)

    def test_create_draft_requires_at_least_one_option(self):

        candidate = self._approved_candidate()
        request = self._draft_request(candidate.id)
        request.options = []

        with self.assertRaises(BadRequestException):
            self.service.create_draft(request, company_id=COMPANY_ID,
                correlation_id="c1")

    # --------------------------------------------------
    # Marketplace / RocketGrowth 분리
    # --------------------------------------------------

    def test_marketplace_and_rocket_growth_are_independent(self):

        candidate_a = self._approved_candidate("CAND-MKT")
        candidate_b = self._approved_candidate("CAND-RG")

        product_a, _, _ = self.service.create_draft(
            self._draft_request(
                candidate_a.id, idempotency_key="idem-mkt",
                sales_method=SalesMethod.MARKETPLACE,
            ),
            company_id=COMPANY_ID,
                correlation_id="c1",
        )
        product_b, _, _ = self.service.create_draft(
            self._draft_request(
                candidate_b.id, idempotency_key="idem-rg",
                sales_method=SalesMethod.ROCKET_GROWTH,
            ),
            company_id=COMPANY_ID,
                correlation_id="c2",
        )

        self.assertEqual(product_a.sales_method, SalesMethod.MARKETPLACE)
        self.assertEqual(product_b.sales_method, SalesMethod.ROCKET_GROWTH)
        self.assertNotEqual(product_a.id, product_b.id)

    def test_unknown_sales_method_rejected(self):

        candidate = self._approved_candidate()
        request = self._draft_request(candidate.id, sales_method="UNKNOWN_METHOD")

        with self.assertRaises(BadRequestException):
            self.service.create_draft(request, company_id=COMPANY_ID,
                correlation_id="c1")

    # --------------------------------------------------
    # 구조화된 옵션 (문자열 한 칸 저장 금지)
    # --------------------------------------------------

    def test_options_are_structured_rows_not_a_single_string(self):

        candidate = self._approved_candidate()
        product, _, _ = self.service.create_draft(
            self._draft_request(candidate.id), company_id=COMPANY_ID,
                correlation_id="c1",
        )

        options = self.service.list_options(product.id, COMPANY_ID)

        self.assertEqual(len(options), 1)
        option = options[0]
        self.assertEqual(option.option_name, "색상")
        self.assertEqual(option.option_value, "블랙")
        self.assertIsInstance(option.price, Decimal)
        self.assertEqual(option.stock, 50)

    # --------------------------------------------------
    # GTIN/MPN 예외 근거
    # --------------------------------------------------

    def test_missing_gtin_mpn_without_exemption_fails_policy_validation(self):

        candidate = self._approved_candidate()
        product, _, _ = self.service.create_draft(
            self._draft_request(
                candidate.id, gtin=None, mpn=None,
                identifier_exemption_reason=None,
            ),
            company_id=COMPANY_ID,
                correlation_id="c1",
        )

        validated, _ = self.service.validate_policy(
            product.id, COMPANY_ID, correlation_id="c2",
        )

        self.assertEqual(validated.status, IntegrationStatus.VALIDATION_FAILED)
        self.assertIn("GTIN/MPN", validated.validation_errors)

    def test_missing_gtin_mpn_with_exemption_reason_passes_identifier_check(self):

        self._seed_baseline_verified_sellable_policy()
        candidate = self._approved_candidate()
        product, _, _ = self.service.create_draft(
            self._draft_request(
                candidate.id, gtin=None, mpn=None,
                identifier_exemption_reason="공산품 예외 — 자체 제작 상품",
            ),
            company_id=COMPANY_ID,
                correlation_id="c1",
        )

        validated, _ = self.service.validate_policy(
            product.id, COMPANY_ID, correlation_id="c2",
        )

        self.assertEqual(validated.status, IntegrationStatus.READY_FOR_REVIEW)

    # --------------------------------------------------
    # 재고 신뢰도 (공급처 재고 != 노출 재고)
    # --------------------------------------------------

    def test_marketplace_exposure_stock_is_supplier_minus_safety(self):

        candidate = self._approved_candidate()
        product, _, _ = self.service.create_draft(
            self._draft_request(
                candidate.id, supplier_stock=100, safety_stock=10,
            ),
            company_id=COMPANY_ID,
                correlation_id="c1",
        )

        self.assertEqual(product.marketplace_exposure_stock, 90)
        self.assertEqual(product.available_stock, 90)
        self.assertFalse(product.stock_review_required)

    def test_stale_stock_check_forces_zero_exposure(self):

        candidate = self._approved_candidate()
        stale_time = datetime.utcnow() - timedelta(hours=48)

        product, _, _ = self.service.create_draft(
            self._draft_request(
                candidate.id, supplier_stock=100, safety_stock=10,
                last_stock_checked_at=stale_time,
            ),
            company_id=COMPANY_ID,
                correlation_id="c1",
        )

        self.assertEqual(product.marketplace_exposure_stock, 0)
        self.assertEqual(product.available_stock, 0)
        self.assertTrue(product.stock_review_required)

    def test_missing_stock_check_timestamp_forces_review(self):

        candidate = self._approved_candidate()
        product, _, _ = self.service.create_draft(
            self._draft_request(
                candidate.id, supplier_stock=100, safety_stock=10,
                last_stock_checked_at=None,
            ),
            company_id=COMPANY_ID,
                correlation_id="c1",
        )

        self.assertEqual(product.marketplace_exposure_stock, 0)
        self.assertTrue(product.stock_review_required)


if __name__ == "__main__":
    unittest.main()
