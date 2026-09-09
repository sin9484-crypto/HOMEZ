"""
=========================================================
Homez OS

File : tests/test_marketplace_adapter_contract.py

채널별 Fulfillment Adapter 검증:
4) 지원하지 않는 채널·방식 조합 거부(NOT_SUPPORTED)
5) 판매자배송 정상 검증

재감사 신규 (2026-07-31):
6) Adapter가 실제 검증된 입력(빈 dict 아님)을 수신하는지 확인
7) Adapter 출력(outbound payload)도 채널별 Schema로 재검증되는지 확인
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.company.model import Company
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.model import (
    AutomationModeState,
    EmergencyStop,
    ExecutionLimit,
    ExecutionPeriodUsage,
    ExecutionUsage,
)
from app.domains.automation_safety.service import SafetyService
from app.domains.marketplace_listing.adapters.coupang_adapter import (
    NOT_SUPPORTED,
)
from app.domains.marketplace_listing.adapters.coupang_adapter import (
    CoupangFulfillmentAdapter,
)
from app.domains.marketplace_listing.adapters.naver_adapter import (
    NaverFulfillmentAdapter,
)
from app.domains.channel_policy.model import ChannelPolicyEvaluation
from app.domains.channel_policy.model import ChannelPolicyRule
from app.domains.channel_policy.model import CompanyChannelPolicySettings
from app.domains.marketplace_listing.approval_service import ApprovalService
from app.domains.marketplace_listing.constants import (
    CapabilityStatus,
    FulfillmentMode,
)
from app.domains.marketplace_listing.model import (
    MarketplaceAccount,
    MarketplaceChannel,
    MarketplaceFulfillmentCapability,
    MarketplaceFulfillmentEligibility,
    MarketplaceFulfillmentSelection,
    MarketplaceListing,
    MarketplaceListingDraft,
    MarketplaceSubmission,
    MarketplaceSubmissionApproval,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceFulfillmentSelectionCreateRequest,
    MarketplaceListingCreateRequest,
    MarketplaceSubmissionApprovalDecisionRequest,
    MarketplaceSubmissionApprovalRequestRequest,
    MarketplaceSubmissionRequest,
)
from app.domains.marketplace_listing.service import MarketplaceListingService
from app.domains.marketplace_listing.submission_service import (
    SubmissionService,
)
from app.domains.media_asset.model import MediaAsset
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.user.model import User  # noqa: F401 (Company.relationship("User") 해석용)
from tests.channel_policy_test_helpers import seed_channel_eligible_policy

_COUNTER = 0


def _next_key(prefix: str) -> str:

    global _COUNTER
    _COUNTER += 1

    return f"{prefix}-{_COUNTER}"


VALID_COUPANG_SELLER_FULFILLED_FIELDS = {
    "deliveryMethod": "AGENT_BUY",
    "deliveryChargeType": "FREE",
    "deliveryCharge": "0",
    "deliveryChargeOnReturn": "2500",
    "returnCharge": "2500",
    "returnCenterCode": "RC001",
    "outboundShippingPlaceCode": "OS001",
    "returnChargeName": "반품지",
    "companyContactNumber": "0212345678",
    "returnZipCode": "12345",
    "returnAddress": "서울시 강남구",
    "items": [{"itemName": "기본형", "externalVendorSku": "SKU-001"}],
    "originalPrice": "10000",
    "salePrice": "9000",
    "maximumBuyCount": 100,
}


class CoupangAdapterTestCase(unittest.TestCase):

    def setUp(self):

        self.adapter = CoupangFulfillmentAdapter()

    def test_seller_fulfilled_maps_to_marketplace_item_data(self):

        result = self.adapter.translate(
            FulfillmentMode.SELLER_FULFILLED, {"sku": "abc"},
        )

        self.assertIn("marketplaceItemData", result)
        self.assertEqual(result["marketplaceItemData"]["sku"], "abc")

    def test_marketplace_fulfilled_maps_to_rocket_growth_item_data(self):

        result = self.adapter.translate(
            FulfillmentMode.MARKETPLACE_FULFILLED, {"sku": "abc"},
        )

        self.assertIn("rocketGrowthItemData", result)
        self.assertEqual(
            result["rocketGrowthAdditionalInformation"]["legalAgreement"],
            "AGREE",
        )

    def test_direct_purchase_is_never_supported(self):

        result = self.adapter.translate(
            FulfillmentMode.DIRECT_PURCHASE, {"sku": "abc"},
        )

        self.assertEqual(result, NOT_SUPPORTED)

    def test_unmodeled_mode_is_not_supported(self):

        result = self.adapter.translate(
            FulfillmentMode.DIGITAL_DELIVERY, {},
        )

        self.assertEqual(result, NOT_SUPPORTED)

    def test_no_network_libraries_imported(self):
        """이 Adapter 모듈은 requests/httpx 등 어떤 네트워크 라이브러리도
        import하지 않는다(app/domains/coupang/gateway.py와 동일 원칙)."""

        import app.domains.marketplace_listing.adapters.coupang_adapter as mod

        with open(mod.__file__, encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn("import requests", source)
        self.assertNotIn("import httpx", source)
        self.assertNotIn("urllib.request", source)


class NaverAdapterTestCase(unittest.TestCase):

    def setUp(self):

        self.adapter = NaverFulfillmentAdapter()

    def test_seller_fulfilled_maps_to_delivery_type(self):

        result = self.adapter.translate(FulfillmentMode.SELLER_FULFILLED, {})

        self.assertEqual(result["deliveryType"], "DELIVERY")
        self.assertEqual(result["deliveryAttributeType"], "NORMAL")

    def test_unverified_modes_are_not_supported(self):
        """네이버는 공식 문서로 SELLER_FULFILLED만 확인됐다 — 다른
        방식은 추측하지 않고 전부 NOT_SUPPORTED다."""

        for mode in (
            FulfillmentMode.MARKETPLACE_FULFILLED,
            FulfillmentMode.DIRECT_PURCHASE,
            FulfillmentMode.PARTNER_FAST_DELIVERY,
        ):
            result = self.adapter.translate(mode, {})
            self.assertEqual(result, NOT_SUPPORTED)

    def test_no_network_libraries_imported(self):

        import app.domains.marketplace_listing.adapters.naver_adapter as mod

        with open(mod.__file__, encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn("import requests", source)
        self.assertNotIn("import httpx", source)


class SubmissionAdapterIntegrationTestCase(unittest.TestCase):
    """
    submission_service.submit() 경로가 실제로 Adapter에 검증된 typed
    data를 넘기고, Adapter 출력을 outbound Schema로 재검증하는지
    end-to-end로 확인한다(6, 7번 시나리오).
    """

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
                MarketplaceChannel.__table__,
                MarketplaceAccount.__table__,
                MarketplaceFulfillmentCapability.__table__,
                MarketplaceListingDraft.__table__,
                MarketplaceListing.__table__,
                MarketplaceFulfillmentSelection.__table__,
                MarketplaceSubmissionApproval.__table__,
                MarketplaceFulfillmentEligibility.__table__,
                MarketplaceSubmission.__table__,
                AutomationModeState.__table__,
                EmergencyStop.__table__,
                ExecutionLimit.__table__,
                ExecutionUsage.__table__,
                ExecutionPeriodUsage.__table__,
                MediaAsset.__table__,
                ChannelPolicyRule.__table__,
                CompanyChannelPolicySettings.__table__,
                ChannelPolicyEvaluation.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        company = Company(
            name="테스트 회사", business_number="777-77-77777", ceo="테스트",
            phone="02-000-0000", email="adapter@example.com", address="서울",
        )
        self.db.add(company)
        self.db.commit()
        self.company_id = company.id

        self.listing_service = MarketplaceListingService(self.db)
        self.approval_service = ApprovalService(self.db)
        self.safety_service = SafetyService(self.db)
        self.submission_service = SubmissionService(
            self.db, safety_service=self.safety_service,
        )
        self.safety_service.set_mode(
            AutomationMode.OPERATOR_APPROVAL, set_by=1, is_admin=True,
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _seed_and_approve(self):

        candidate = ProductCandidate(
            candidate_key="test:COUPANG:CAND-1", source_type="TREND",
            source_reference="CAND-1", market="COUPANG",
            product_name="테스트 상품", status=CandidateStatus.APPROVED,
        )
        self.db.add(candidate)
        self.db.commit()

        channel = MarketplaceChannel(
            code="COUPANG", name="쿠팡", doc_verification_status="VERIFIED",
        )
        self.db.add(channel)
        self.db.commit()

        account = MarketplaceAccount(
            company_id=self.company_id, channel_id=channel.id,
            account_code="a1", account_name="계정",
        )
        self.db.add(account)
        self.db.commit()

        capability = MarketplaceFulfillmentCapability(
            channel_id=channel.id,
            fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
            is_supported=True, requires_eligibility_check=False,
            requires_account_contract=False,
            external_display_name="판매자 배송", policy_version="1.0.0",
            schema_name="coupang_seller_fulfilled", schema_version="1.0.0",
            doc_source_reference="test", status=CapabilityStatus.VERIFIED,
            verified_at=datetime.utcnow(),
        )
        self.db.add(capability)
        self.db.commit()

        listing, _dup = self.listing_service.create_listing(
            MarketplaceListingCreateRequest(
                product_candidate_id=candidate.id,
                marketplace_account_id=account.id,
            ),
            self.company_id,
        )
        selection, _dup, _events = self.listing_service.select_fulfillment_mode(
            MarketplaceFulfillmentSelectionCreateRequest(
                listing_id=listing.id,
                fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                required_fields=VALID_COUPANG_SELLER_FULFILLED_FIELDS,
                idempotency_key=_next_key("sel"),
            ),
            selected_by=1, company_id=self.company_id,
        )

        pending, _dup = self.approval_service.request_approval(
            MarketplaceSubmissionApprovalRequestRequest(
                listing_id=listing.id, selection_id=selection.id,
                planned_quantity=10, unit_price=Decimal("9000"),
                unit_cost_of_goods=Decimal("5000"),
                expected_logistics_cost=Decimal("500"),
                idempotency_key=_next_key("appreq"),
            ),
            requested_by=1, company_id=self.company_id,
        )
        self.approval_service.approve(
            pending.id,
            MarketplaceSubmissionApprovalDecisionRequest(
                expires_at=datetime.utcnow() + timedelta(days=1),
                idempotency_key=_next_key("appdec"),
            ),
            approved_by=99, company_id=self.company_id,
        )

        seed_channel_eligible_policy(
            self.db, company_id=self.company_id,
            product_candidate_id=candidate.id,
            channel=channel.code, selection_id=selection.id,
        )

        return listing, selection

    # ----------------------------------------------------
    # 6) Adapter가 실제 검증된 입력을 수신한다
    # ----------------------------------------------------

    def test_adapter_receives_real_validated_payload_not_empty_dict(self):

        listing, selection = self._seed_and_approve()

        with mock.patch.object(
            CoupangFulfillmentAdapter, "translate",
            wraps=CoupangFulfillmentAdapter.translate,
        ) as spy:
            self.submission_service.submit(
                MarketplaceSubmissionRequest(
                    listing_id=listing.id, selection_id=selection.id,
                    idempotency_key=_next_key("sub"),
                ),
                self.company_id,
            )

        mode_arg, payload_arg = spy.call_args[0]

        self.assertEqual(mode_arg, FulfillmentMode.SELLER_FULFILLED)
        self.assertNotEqual(payload_arg, {})
        self.assertEqual(payload_arg["maximumBuyCount"], 100)
        self.assertEqual(payload_arg["items"][0]["itemName"], "기본형")

    # ----------------------------------------------------
    # 7) outbound payload Schema 검증
    # ----------------------------------------------------

    def test_outbound_payload_failing_schema_blocks_submission(self):
        """
        Adapter가 (버그로) outbound Schema와 맞지 않는 모양을 반환하면
        제출이 차단되어야 한다 — Adapter 출력을 무조건 신뢰하지 않는다.
        """

        listing, selection = self._seed_and_approve()

        with mock.patch.object(
            CoupangFulfillmentAdapter, "translate",
            return_value={"unexpectedKey": "broken adapter output"},
        ):
            result = self.submission_service.submit(
                MarketplaceSubmissionRequest(
                    listing_id=listing.id, selection_id=selection.id,
                    idempotency_key=_next_key("sub"),
                ),
                self.company_id,
            )

        self.assertEqual(result.status, "FAILED")
        self.assertIn("outbound 페이로드 검증 실패", result.error_reason)

    def test_outbound_payload_matching_schema_allows_submission(self):

        listing, selection = self._seed_and_approve()

        result = self.submission_service.submit(
            MarketplaceSubmissionRequest(
                listing_id=listing.id, selection_id=selection.id,
                idempotency_key=_next_key("sub"),
            ),
            self.company_id,
        )

        self.assertEqual(result.status, "PENDING")


if __name__ == "__main__":
    unittest.main()
