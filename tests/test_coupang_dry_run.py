"""
=========================================================
Homez OS

File : tests/test_coupang_dry_run.py

HOMEZ V3.1 Coupang Marketplace Integration Foundation
Dry Run 검증: 외부 네트워크 0회, Secret 접근 0회, 필수 필드 누락 시
실패, 완전한 Payload만 통과, 실제 상품등록 상태로 전이하지 않음,
멱등성.
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
from app.domains.coupang.constants import DryRunOutcome
from app.domains.coupang.constants import IntegrationStatus
from app.domains.coupang.constants import PolicyMatchField
from app.domains.coupang.constants import PolicySetStatus
from app.domains.coupang.constants import RiskLevel
from app.domains.coupang.gateway import REQUIRED_PAYLOAD_FIELDS
from app.domains.coupang.gateway import CoupangDryRunGateway
from app.domains.coupang.model import CoupangDryRunAttempt
from app.domains.coupang.model import CoupangIntegrationDecision
from app.domains.coupang.model import CoupangMarketplaceProduct
from app.domains.coupang.model import CoupangPolicyRule
from app.domains.coupang.model import CoupangPolicySet
from app.domains.coupang.model import CoupangProductNotice
from app.domains.coupang.model import CoupangProductOption
from app.domains.coupang.model import CoupangProfitEstimate
from app.domains.coupang.schema import CoupangDraftCreateRequest
from app.domains.coupang.schema import CoupangProductNoticeInput
from app.domains.coupang.schema import CoupangProductOptionInput
from app.domains.coupang.schema import CoupangProfitEstimateRequest
from app.domains.coupang.service import CoupangIntegrationService
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection



COMPANY_ID = 1

class CoupangDryRunGatewayUnitTestCase(unittest.TestCase):
    """Gateway 자체 단위 테스트 — DB 없이 검증한다."""

    def setUp(self):

        self.gateway = CoupangDryRunGateway()

    def test_gateway_module_imports_no_network_library(self):
        """
        docstring/주석에는 "requests/httpx를 import하지 않는다"는 설명
        자체가 등장할 수 있으므로, 부분 문자열 검사 대신 실제 import
        구문(ast)만 검사한다.
        """

        import app.domains.coupang.gateway as gateway_module

        source = inspect.getsource(gateway_module)
        tree = ast.parse(source)

        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)

        forbidden_prefixes = (
            "requests", "httpx", "urllib", "socket", "http.client", "http",
        )
        for module_name in imported_modules:
            for forbidden in forbidden_prefixes:
                self.assertFalse(
                    module_name == forbidden
                    or module_name.startswith(forbidden + "."),
                    f"gateway.py가 네트워크 라이브러리({module_name})를 "
                    "import합니다 — Dry Run은 네트워크 호출을 하면 안 "
                    "됩니다.",
                )

    def test_submit_dry_run_signature_has_no_credential_parameters(self):

        signature = inspect.signature(self.gateway.submit_dry_run)
        param_names = {name.lower() for name in signature.parameters}

        for forbidden in (
            "access_key", "secret", "secret_key", "api_key", "token",
        ):
            self.assertNotIn(forbidden, param_names)

    def test_incomplete_payload_fails(self):

        result = self.gateway.submit_dry_run({"sales_method": "MARKETPLACE"})

        self.assertEqual(result.outcome, DryRunOutcome.FAILED)
        self.assertGreater(len(result.errors), 0)

    def test_complete_payload_passes(self):

        payload = {
            field_name: "값" if field_name != "options" else [{"a": 1}]
            for field_name in REQUIRED_PAYLOAD_FIELDS
        }

        result = self.gateway.submit_dry_run(payload)

        self.assertEqual(result.outcome, DryRunOutcome.PASSED)
        self.assertEqual(result.errors, [])

    def test_empty_options_list_fails_even_if_present(self):

        payload = {
            field_name: "값" if field_name != "options" else []
            for field_name in REQUIRED_PAYLOAD_FIELDS
        }

        result = self.gateway.submit_dry_run(payload)

        self.assertEqual(result.outcome, DryRunOutcome.FAILED)


class CoupangDryRunServiceTestCase(unittest.TestCase):
    """Service를 통한 전체 흐름(초안→정책→수익성→Dry Run) 검증."""

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

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _seed_baseline_verified_sellable_policy(self):

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

    @staticmethod
    def _verified_notice() -> CoupangProductNoticeInput:

        return CoupangProductNoticeInput(
            notice_category_name="생활용품",
            notice_category_detail_name="일반공산품",
            content="품명: 테스트 상품 / 제조자: HOMEZ 테스트",
            source="test-source",
            verified_at=datetime.utcnow(),
            status="VERIFIED",
        )

    def _dry_run_ready_product(self, idempotency_key="idem-1", with_notice=True):

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

        product, _dup, _events = self.service.create_draft(
            CoupangDraftCreateRequest(
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
                        option_name="기본", option_value="기본",
                        vendor_sku=f"SKU-{idempotency_key}-A",
                        price=Decimal("29900"), stock=10,
                    ),
                ],
                notices=[self._verified_notice()] if with_notice else [],
            ),
            company_id=COMPANY_ID,
                correlation_id="c1",
        )
        product, _events = self.service.validate_policy(
            product.id, COMPANY_ID, correlation_id="c2",
        )
        self.assertEqual(product.status, IntegrationStatus.READY_FOR_REVIEW)

        self.service.estimate_profit(
            product.id,
                COMPANY_ID,
            CoupangProfitEstimateRequest(
                consumer_sale_price=Decimal("29900"),
                supplier_product_cost=Decimal("10000"),
                supplier_shipping_cost=Decimal("2000"),
                coupang_sales_fee=Decimal("2000"),
            ),
            correlation_id="c3",
        )

        return product

    def test_dry_run_passes_and_stops_at_dry_run_passed(self):

        product = self._dry_run_ready_product()

        result_product, result, duplicate, _events = self.service.run_dry_run(
            product.id, COMPANY_ID, idempotency_key="dr-1", correlation_id="c4",
        )

        self.assertFalse(duplicate)
        self.assertEqual(result.outcome, DryRunOutcome.PASSED)
        self.assertEqual(result_product.status, IntegrationStatus.DRY_RUN_PASSED)

        # READY_FOR_SUBMISSION은 최종 승인을 거쳐야만 도달한다 — Dry Run
        # 통과만으로는 그 이후 상태(SUBMITTED 등)로 자동 전이하지 않는다.
        self.assertNotIn(
            result_product.status,
            (
                IntegrationStatus.SUBMITTED,
                IntegrationStatus.REVIEWING,
                IntegrationStatus.APPROVED,
                IntegrationStatus.READY_FOR_SUBMISSION,
            ),
        )

    def test_dry_run_fails_when_verified_notice_missing(self):
        """
        고시정보(CoupangProductNotice)가 VERIFIED 상태로 하나도 없으면
        Dry Run은 필수 필드 누락으로 실패해야 한다 — 재감사 Medium-1
        반영(상품 카테고리에 필요한 고시정보가 없으면 Dry Run을
        실패시킨다).
        """

        product = self._dry_run_ready_product(
            idempotency_key="no-notice", with_notice=False,
        )

        _product, result, _dup, _events = self.service.run_dry_run(
            product.id, COMPANY_ID, idempotency_key="dr-no-notice", correlation_id="c4",
        )

        self.assertEqual(result.outcome, DryRunOutcome.FAILED)
        self.assertTrue(
            any("notices" in err for err in result.errors),
            f"고시정보 누락이 필수 필드 오류로 나타나야 합니다: {result.errors}",
        )

    def test_dry_run_passes_when_verified_notice_present(self):

        product = self._dry_run_ready_product(
            idempotency_key="with-notice", with_notice=True,
        )

        _product, result, _dup, _events = self.service.run_dry_run(
            product.id, COMPANY_ID, idempotency_key="dr-with-notice",
            correlation_id="c4",
        )

        self.assertEqual(result.outcome, DryRunOutcome.PASSED)

    def test_dry_run_without_profit_estimate_is_blocked(self):

        candidate = ProductCandidate(
            candidate_key="COUPANG_API:COUPANG:NO-PROFIT",
            source_type="COUPANG_API",
            market="COUPANG",
            source_reference="NO-PROFIT",
            product_name="수익성 미계산 상품",
            status=CandidateStatus.APPROVED,
        )
        self.db.add(candidate)
        self.db.commit()

        product, _dup, _events = self.service.create_draft(
            CoupangDraftCreateRequest(
                product_candidate_id=candidate.id,
                sales_method="MARKETPLACE",
                external_vendor_sku="SKU-NO-PROFIT",
                seller_product_name="수익성 미계산 상품",
                idempotency_key="idem-no-profit",
                brand="HOMEZ",
                display_category_code="CAT-001",
                gtin="8801234567890",
                sale_price=Decimal("29900"),
                shipping_method="COURIER",
                outbound_shipping_place_code="OUT-1",
                return_center_code="RET-1",
                options=[
                    CoupangProductOptionInput(
                        option_name="기본", option_value="기본",
                        vendor_sku="SKU-NO-PROFIT-A",
                        price=Decimal("29900"), stock=10,
                    ),
                ],
            ),
            company_id=COMPANY_ID,
                correlation_id="c1",
        )
        self.service.validate_policy(product.id, COMPANY_ID, correlation_id="c2")

        with self.assertRaises(BadRequestException):
            self.service.run_dry_run(
                product.id, COMPANY_ID, idempotency_key="dr-x", correlation_id="c3",
            )

    def test_dry_run_is_idempotent_duplicate_returns_same_result(self):

        product = self._dry_run_ready_product()

        first_product, first_result, first_dup, _ = self.service.run_dry_run(
            product.id, COMPANY_ID, idempotency_key="dr-dup", correlation_id="c4",
        )
        second_product, second_result, second_dup, _ = (
            self.service.run_dry_run(
                product.id, COMPANY_ID, idempotency_key="dr-dup", correlation_id="c5",
            )
        )

        self.assertFalse(first_dup)
        self.assertTrue(second_dup)
        self.assertEqual(first_result.outcome, second_result.outcome)
        self.assertEqual(second_product.status, first_product.status)

    def test_dry_run_gateway_makes_zero_network_calls_end_to_end(self):
        """
        서비스 전체 흐름을 실행하는 동안 socket.socket 생성이 0회임을
        확인한다 — Dry Run 경로 어디에서도 실제 네트워크 연결을 시도하지
        않는다.
        """

        import socket
        from unittest import mock

        product = self._dry_run_ready_product()

        with mock.patch.object(
            socket, "socket", side_effect=AssertionError("네트워크 호출 감지됨"),
        ):
            _p, result, _dup, _events = self.service.run_dry_run(
                product.id, COMPANY_ID, idempotency_key="dr-net-check",
                correlation_id="c4",
            )

        self.assertEqual(result.outcome, DryRunOutcome.PASSED)


if __name__ == "__main__":
    unittest.main()
