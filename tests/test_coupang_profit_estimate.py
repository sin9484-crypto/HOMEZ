"""
=========================================================
Homez OS

File : tests/test_coupang_profit_estimate.py

HOMEZ V3.1 Coupang Marketplace Integration Foundation
수익성 계산 검증: Decimal 강제, net == gross - fee 불변식, 최소 마진
차단, UNKNOWN 수수료 차단, required_funding, 예상 정산이 Funding에
반영되지 않음(Domain 경계).
=========================================================
"""

import ast
import inspect
import os
import tempfile
import unittest
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.database.base import Base
from app.domains.coupang.constants import IntegrationStatus
from app.domains.coupang.constants import PolicyMatchField
from app.domains.coupang.constants import PolicySetStatus
from app.domains.coupang.constants import RiskLevel
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
from app.domains.coupang.schema import CoupangProfitEstimateRequest
from app.domains.coupang.service import CoupangIntegrationService
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection



COMPANY_ID = 1
class CoupangProfitEstimateTestCase(unittest.TestCase):
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
        self._seed_baseline_verified_sellable_policy()

    def _seed_baseline_verified_sellable_policy(self):
        """
        CAT-001을 명시적으로 SELLABLE로 확정하는 VERIFIED/활성/완전/
        유효기간 내 정책 세트 — 재감사 반영 이후 정책 검증은
        fail-closed이므로, READY_FOR_REVIEW까지 도달하려면 이 시드가
        필요하다.
        """

        policy_set = CoupangPolicySet(
            policy_set_id="test-verified-baseline",
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

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _ready_for_review_product(self, idempotency_key="idem-1"):

        candidate = ProductCandidate(
            candidate_key=f"COUPANG_API:COUPANG:{idempotency_key}",
            source_type="COUPANG_API",
            market="COUPANG",
            source_reference=idempotency_key,
            product_name="테스트 상품",
            status=CandidateStatus.APPROVED,
        )
        self.db.add(candidate)
        self.db.commit()

        request = CoupangDraftCreateRequest(
            product_candidate_id=candidate.id,
            sales_method="MARKETPLACE",
            external_vendor_sku=f"SKU-{idempotency_key}",
            seller_product_name="테스트 상품",
            idempotency_key=idempotency_key,
            brand="HOMEZ",
            display_category_code="CAT-001",
            gtin="8801234567890",
            sale_price=Decimal("29900"),
            shipping_method="COURIER",
            outbound_shipping_place_code="OUT-1",
            return_center_code="RET-1",
            options=[
                CoupangProductOptionInput(
                    option_name="기본",
                    option_value="기본",
                    vendor_sku=f"SKU-{idempotency_key}-A",
                    price=Decimal("29900"),
                    stock=10,
                ),
            ],
        )
        product, _dup, _events = self.service.create_draft(
            request, company_id=COMPANY_ID,
                correlation_id="c1",
        )
        product, _events = self.service.validate_policy(
            product.id, COMPANY_ID, correlation_id="c2",
        )
        self.assertEqual(product.status, IntegrationStatus.READY_FOR_REVIEW)

        return product

    def _good_margin_request(self, **overrides) -> CoupangProfitEstimateRequest:

        base = dict(
            consumer_sale_price=Decimal("29900"),
            supplier_product_cost=Decimal("10000"),
            supplier_shipping_cost=Decimal("2000"),
            coupang_sales_fee=Decimal("2000"),
            coupang_shipping_cost=Decimal("500"),
            advertising_cost=Decimal("500"),
        )
        base.update(overrides)

        return CoupangProfitEstimateRequest(**base)

    # --------------------------------------------------
    # Decimal 강제
    # --------------------------------------------------

    def test_all_monetary_outputs_are_decimal(self):

        product = self._ready_for_review_product()
        estimate, _events = self.service.estimate_profit(
            product.id, COMPANY_ID, self._good_margin_request(), correlation_id="c3",
        )

        for field_name in (
            "gross_revenue", "marketplace_fee_total",
            "supplier_payment_estimate", "expected_net_settlement",
            "expected_profit", "expected_margin_rate", "required_funding",
            "break_even_price",
        ):
            value = getattr(estimate, field_name)
            self.assertIsInstance(
                value, Decimal, f"{field_name}이 Decimal이 아닙니다: {type(value)}",
            )

    def test_service_module_does_not_use_float_literals_for_money(self):
        """
        service.py 소스에 실수 리터럴(Float 계산 흔적)이 없는지 AST로
        확인한다 — Decimal만 사용해야 한다.
        """

        import app.domains.coupang.service as service_module

        source = inspect.getsource(service_module)
        tree = ast.parse(source)

        float_constants = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, float)
        ]

        self.assertEqual(
            float_constants, [],
            "service.py에 float 리터럴이 발견되었습니다 — Decimal만 "
            "사용해야 합니다.",
        )

    # --------------------------------------------------
    # net == gross - fee 불변식
    # --------------------------------------------------

    def test_net_settlement_equals_gross_minus_fee(self):

        product = self._ready_for_review_product()
        estimate, _events = self.service.estimate_profit(
            product.id, COMPANY_ID, self._good_margin_request(), correlation_id="c3",
        )

        self.assertEqual(
            estimate.expected_net_settlement,
            estimate.gross_revenue - estimate.marketplace_fee_total,
        )

    def test_marketplace_fee_total_sums_all_fee_components(self):

        product = self._ready_for_review_product()
        request = self._good_margin_request(
            coupang_sales_fee=Decimal("1000"),
            coupang_shipping_cost=Decimal("500"),
            advertising_cost=Decimal("300"),
            coupon_cost=Decimal("200"),
            expected_return_cost=Decimal("100"),
            rocket_growth_cost=Decimal("0"),
            other_deductions=Decimal("50"),
            vat_or_tax_estimate=Decimal("50"),
        )
        estimate, _events = self.service.estimate_profit(
            product.id, COMPANY_ID, request, correlation_id="c3",
        )

        self.assertEqual(estimate.marketplace_fee_total, Decimal("2200.00"))

    # --------------------------------------------------
    # UNKNOWN 수수료 차단
    # --------------------------------------------------

    def test_unknown_sales_fee_blocks_calculation(self):

        product = self._ready_for_review_product()

        with self.assertRaises(BadRequestException):
            self.service.estimate_profit(
                product.id,
                COMPANY_ID,
                self._good_margin_request(coupang_sales_fee=None),
                correlation_id="c3",
            )

        # 계산 자체가 차단되었으므로 행이 생성되지 않아야 한다.
        self.assertEqual(
            self.service.list_profit_estimates(product.id, COMPANY_ID), [],
        )

    # --------------------------------------------------
    # 최소 마진 미달 → 자동 진행 차단
    # --------------------------------------------------

    def test_margin_below_minimum_blocks_further_progress(self):

        product = self._ready_for_review_product()

        # 수수료가 매우 높아 마진이 최소 기준(5%) 미만이 되도록 구성.
        estimate, _events = self.service.estimate_profit(
            product.id,
                COMPANY_ID,
            self._good_margin_request(
                supplier_product_cost=Decimal("20000"),
                coupang_sales_fee=Decimal("9000"),
            ),
            correlation_id="c3",
        )

        self.assertFalse(estimate.meets_minimum_margin)

        product = self.service.get(product.id, COMPANY_ID)
        self.assertEqual(product.status, IntegrationStatus.VALIDATION_FAILED)

        # 마진 미달 상태에서는 Dry Run을 진행할 수 없다.
        with self.assertRaises(BadRequestException):
            self.service.run_dry_run(
                product.id, COMPANY_ID, idempotency_key="dr-1", correlation_id="c4",
            )

    def test_margin_above_minimum_keeps_ready_for_review(self):

        product = self._ready_for_review_product()

        estimate, _events = self.service.estimate_profit(
            product.id, COMPANY_ID, self._good_margin_request(), correlation_id="c3",
        )

        self.assertTrue(estimate.meets_minimum_margin)

        product = self.service.get(product.id, COMPANY_ID)
        self.assertEqual(product.status, IntegrationStatus.READY_FOR_REVIEW)

    def test_estimate_requires_ready_for_review_status(self):

        candidate = ProductCandidate(
            candidate_key="COUPANG_API:COUPANG:DRAFT-ONLY",
            source_type="COUPANG_API",
            market="COUPANG",
            source_reference="DRAFT-ONLY",
            product_name="초안 상태 상품",
            status=CandidateStatus.APPROVED,
        )
        self.db.add(candidate)
        self.db.commit()

        product, _dup, _events = self.service.create_draft(
            CoupangDraftCreateRequest(
                product_candidate_id=candidate.id,
                sales_method="MARKETPLACE",
                external_vendor_sku="SKU-DRAFT-ONLY",
                seller_product_name="초안 상태 상품",
                idempotency_key="idem-draft-only",
                options=[
                    CoupangProductOptionInput(
                        option_name="기본", option_value="기본",
                        vendor_sku="SKU-DRAFT-ONLY-A",
                        price=Decimal("1000"), stock=1,
                    ),
                ],
            ),
            company_id=COMPANY_ID,
                correlation_id="c1",
        )

        with self.assertRaises(BadRequestException):
            self.service.estimate_profit(
                product.id, COMPANY_ID, self._good_margin_request(), correlation_id="c2",
            )

    # --------------------------------------------------
    # required_funding = 공급처 지급 예상치
    # --------------------------------------------------

    def test_required_funding_equals_supplier_payment_estimate(self):

        product = self._ready_for_review_product()
        estimate, _events = self.service.estimate_profit(
            product.id, COMPANY_ID, self._good_margin_request(), correlation_id="c3",
        )

        self.assertEqual(
            estimate.required_funding, estimate.supplier_payment_estimate,
        )
        self.assertEqual(
            estimate.supplier_payment_estimate,
            estimate.supplier_product_cost + estimate.supplier_shipping_cost,
        )

    # --------------------------------------------------
    # 예상 정산이 Funding에 반영되지 않음 (Domain 경계)
    # --------------------------------------------------

    def test_coupang_domain_does_not_import_funding_or_settlement_models(self):
        """
        예상 정산(estimate_profit)은 어떤 경우에도 FundingAccount/
        FundingLedger/MarketplaceSettlement을 직접 수정하지 않는다 —
        소스 레벨에서 해당 Model을 import조차 하지 않음을 강제한다.
        """

        import app.domains.coupang.model as coupang_model
        import app.domains.coupang.service as coupang_service

        for module in (coupang_model, coupang_service):
            source = inspect.getsource(module)
            self.assertNotIn("app.domains.funding", source)
            self.assertNotIn("app.domains.settlement", source)
            self.assertNotIn("app.domains.order", source)
            self.assertNotIn("app.domains.purchase", source)


if __name__ == "__main__":
    unittest.main()
