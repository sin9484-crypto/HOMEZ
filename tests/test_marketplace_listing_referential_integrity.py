"""
=========================================================
Homez OS

File : tests/test_marketplace_listing_referential_integrity.py

데이터 무결성 재감사 신규 테스트 (2026-07-31):
FK를 쓰지 않는 이 코드베이스의 컨벤션(app/database/에서 PRAGMA
foreign_keys를 켜지 않아 실질적 보호가 되지 않음)에 맞춰, 서비스
레벨에서 강제되는 참조 무결성을 검증한다.

11) 고아 생성 차단
12) 잘못된 account/channel 조합 거부
+ 전체 체인 시딩 후 모든 논리참조 컬럼에 대한 고아 0건 스캔
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException, ConflictException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.channel_policy.model import ChannelPolicyEvaluation
from app.domains.channel_policy.model import ChannelPolicyRule
from app.domains.channel_policy.model import CompanyChannelPolicySettings
from app.domains.media_asset.model import MediaAsset
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
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.model import (
    AutomationModeState,
    EmergencyStop,
    ExecutionLimit,
    ExecutionPeriodUsage,
    ExecutionUsage,
)
from app.domains.automation_safety.service import SafetyService
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from tests.channel_policy_test_helpers import seed_channel_eligible_policy
from app.domains.user.model import User  # noqa: F401 (Company.relationship("User") 해석용)

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


class MarketplaceReferentialIntegrityTestCase(unittest.TestCase):

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
            name="테스트 회사", business_number="999-99-99999", ceo="테스트",
            phone="02-000-0000", email="refint@example.com", address="서울",
        )
        self.db.add(company)
        self.db.commit()
        self.company_id = company.id

        self.service = MarketplaceListingService(self.db)
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

    def _candidate(self, ref="CAND-1") -> ProductCandidate:

        candidate = ProductCandidate(
            candidate_key=f"test:COUPANG:{ref}", source_type="TREND",
            source_reference=ref, market="COUPANG",
            product_name="테스트 상품", status=CandidateStatus.APPROVED,
        )
        self.db.add(candidate)
        self.db.commit()

        return candidate

    def _channel(self, code="COUPANG", name="쿠팡", is_active=True):

        channel = MarketplaceChannel(
            code=code, name=name, doc_verification_status="VERIFIED",
            is_active=is_active,
        )
        self.db.add(channel)
        self.db.commit()

        return channel

    def _account(self, channel_id: int, code="a1", is_active=True):

        account = MarketplaceAccount(
            company_id=self.company_id, channel_id=channel_id,
            account_code=code, account_name="계정",
            is_active=is_active,
        )
        self.db.add(account)
        self.db.commit()

        return account

    def _capability(self, channel_id: int) -> MarketplaceFulfillmentCapability:

        capability = MarketplaceFulfillmentCapability(
            channel_id=channel_id,
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

        return capability

    # ----------------------------------------------------
    # 11) 고아 생성 차단
    # ----------------------------------------------------

    def test_create_account_under_inactive_channel_is_blocked(self):

        channel = self._channel(is_active=False)

        with self.assertRaises(BadRequestException):
            self.service.create_account(
                channel.id, "a1", "계정", self.company_id,
            )

    def test_deactivate_account_with_active_listing_is_blocked(self):

        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)

        self.service.create_listing(
            MarketplaceListingCreateRequest(
                product_candidate_id=candidate.id,
                marketplace_account_id=account.id,
            ),
            self.company_id,
        )

        with self.assertRaises(ConflictException):
            self.service.deactivate_account(
                account.id, _next_key("deact"), self.company_id,
            )

    def test_deactivate_account_without_listings_succeeds(self):

        channel = self._channel()
        account = self._account(channel.id)

        deactivated = self.service.deactivate_account(
            account.id, _next_key("deact"), self.company_id,
        )

        self.assertFalse(deactivated.is_active)

    # ----------------------------------------------------
    # 12) 잘못된 account/channel 조합 거부
    # ----------------------------------------------------

    def test_listing_creation_with_nonexistent_account_is_rejected(self):

        candidate = self._candidate()

        with self.assertRaises(BadRequestException):
            self.service.create_listing(
                MarketplaceListingCreateRequest(
                    product_candidate_id=candidate.id,
                    marketplace_account_id=999999,
                ),
                self.company_id,
            )

    def test_listing_creation_with_inactive_account_is_rejected(self):

        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id, is_active=False)

        with self.assertRaises(BadRequestException):
            self.service.create_listing(
                MarketplaceListingCreateRequest(
                    product_candidate_id=candidate.id,
                    marketplace_account_id=account.id,
                ),
                self.company_id,
            )

    # ----------------------------------------------------
    # 전체 체인 시딩 후 고아 0건 스캔
    # ----------------------------------------------------

    def test_full_chain_has_zero_orphans_after_seeding(self):

        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)

        listing, _dup = self.service.create_listing(
            MarketplaceListingCreateRequest(
                product_candidate_id=candidate.id,
                marketplace_account_id=account.id,
            ),
            self.company_id,
        )
        selection, _dup, _events = self.service.select_fulfillment_mode(
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
        self.submission_service.submit(
            MarketplaceSubmissionRequest(
                listing_id=listing.id, selection_id=selection.id,
                idempotency_key=_next_key("sub"),
            ),
            self.company_id,
        )

        orphan_queries = [
            (
                "accounts.channel_id -> channels.id",
                "SELECT COUNT(*) FROM marketplace_accounts "
                "WHERE channel_id NOT IN (SELECT id FROM marketplace_channels)",
            ),
            (
                "capabilities.channel_id -> channels.id",
                "SELECT COUNT(*) FROM marketplace_fulfillment_capabilities "
                "WHERE channel_id NOT IN (SELECT id FROM marketplace_channels)",
            ),
            (
                "listings.product_candidate_id -> product_candidates.id",
                "SELECT COUNT(*) FROM marketplace_listings "
                "WHERE product_candidate_id NOT IN "
                "(SELECT id FROM product_candidates)",
            ),
            (
                "listings.marketplace_account_id -> accounts.id",
                "SELECT COUNT(*) FROM marketplace_listings "
                "WHERE marketplace_account_id NOT IN "
                "(SELECT id FROM marketplace_accounts)",
            ),
            (
                "selections.listing_id -> listings.id",
                "SELECT COUNT(*) FROM marketplace_fulfillment_selections "
                "WHERE listing_id NOT IN (SELECT id FROM marketplace_listings)",
            ),
            (
                "selections.capability_id -> capabilities.id",
                "SELECT COUNT(*) FROM marketplace_fulfillment_selections "
                "WHERE capability_id NOT IN "
                "(SELECT id FROM marketplace_fulfillment_capabilities)",
            ),
            (
                "approvals.listing_id -> listings.id",
                "SELECT COUNT(*) FROM marketplace_submission_approvals "
                "WHERE listing_id NOT IN (SELECT id FROM marketplace_listings)",
            ),
            (
                "approvals.selection_id -> selections.id",
                "SELECT COUNT(*) FROM marketplace_submission_approvals "
                "WHERE selection_id NOT IN "
                "(SELECT id FROM marketplace_fulfillment_selections)",
            ),
            (
                "submissions.listing_id -> listings.id",
                "SELECT COUNT(*) FROM marketplace_submissions "
                "WHERE listing_id NOT IN (SELECT id FROM marketplace_listings)",
            ),
            (
                "submissions.selection_id -> selections.id",
                "SELECT COUNT(*) FROM marketplace_submissions "
                "WHERE selection_id NOT IN "
                "(SELECT id FROM marketplace_fulfillment_selections)",
            ),
            (
                "submissions.marketplace_account_id -> accounts.id",
                "SELECT COUNT(*) FROM marketplace_submissions "
                "WHERE marketplace_account_id NOT IN "
                "(SELECT id FROM marketplace_accounts)",
            ),
        ]

        from sqlalchemy import text

        for label, query in orphan_queries:
            count = self.db.execute(text(query)).scalar()
            self.assertEqual(count, 0, f"고아 발견: {label}")


if __name__ == "__main__":
    unittest.main()
