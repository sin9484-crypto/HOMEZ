"""
=========================================================
Homez OS

File : tests/test_marketplace_tenant_isolation.py

2026-08-01 CTO 3차 지적 — MarketplaceListing Domain 전체 회사 소유권
격리 신규 테스트. 회사 A/B를 별도로 만들고, 전 경로(Listing 조회/
pause/resume, Approval 요청/승인/거절/취소, Submission 제출/이력,
candidate summary, current-selection)가 company_id로 격리되는지
검증한다. 조회만 회사 범위이고 UPDATE는 id만 쓰는 절반짜리 수정이
아님을 확인한다 — 상태 전이(pause/approve/reject/revoke)까지 전부
포함한다.

MarketplaceChannel/MarketplaceFulfillmentCapability는 전역(company_id
없음)이므로 두 회사가 동일한 채널·캡빌리티를 공유해도 안전하다는
전제 자체를 검증하지는 않는다(설계상 의도된 전역 공유 — 이 Domain의
company_id 스코프 대상이 아니다).
=========================================================
"""

import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.automation_safety.service import SafetyService
from app.domains.company.model import Company
from app.domains.channel_policy.model import ChannelPolicyEvaluation
from app.domains.channel_policy.model import ChannelPolicyRule
from app.domains.channel_policy.model import CompanyChannelPolicySettings
from app.domains.media_asset.model import MediaAsset
from app.domains.marketplace_listing.approval_service import ApprovalService
from app.domains.marketplace_listing.constants import ApprovalStatus
from app.domains.marketplace_listing.constants import CapabilityStatus
from app.domains.marketplace_listing.constants import FulfillmentMode
from app.domains.marketplace_listing.eligibility_service import (
    EligibilityService,
)
from app.domains.marketplace_listing.model import MarketplaceAccount
from app.domains.marketplace_listing.model import MarketplaceChannel
from app.domains.marketplace_listing.model import (
    MarketplaceFulfillmentCapability,
)
from app.domains.marketplace_listing.model import (
    MarketplaceFulfillmentEligibility,
)
from app.domains.marketplace_listing.model import (
    MarketplaceFulfillmentSelection,
)
from app.domains.marketplace_listing.model import MarketplaceListing
from app.domains.marketplace_listing.model import MarketplaceListingDraft
from app.domains.marketplace_listing.model import MarketplaceSubmission
from app.domains.marketplace_listing.model import (
    MarketplaceSubmissionApproval,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceFulfillmentSelectionCreateRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceListingCreateRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionApprovalDecisionRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionApprovalRejectionRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionApprovalRequestRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionRequest,
)
from app.domains.marketplace_listing.service import MarketplaceListingService
from app.domains.marketplace_listing.submission_service import (
    SubmissionService,
)
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


class MarketplaceTenantIsolationTestCase(unittest.TestCase):
    """
    회사 A/B 전 경로 404 + 상태 불변 + idempotency 독립성 검증.
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

        self.listing_service = MarketplaceListingService(self.db)
        self.approval_service = ApprovalService(self.db)
        self.eligibility_service = EligibilityService(self.db)
        self.safety_service = SafetyService(self.db)
        self.submission_service = SubmissionService(
            self.db, safety_service=self.safety_service,
        )
        self.safety_service.set_mode(
            AutomationMode.OPERATOR_APPROVAL, set_by=1, is_admin=True,
        )

        # 전역 채널·캡빌리티(company_id 없음) — 두 회사가 공유한다.
        self.channel = MarketplaceChannel(
            code="COUPANG", name="쿠팡", doc_verification_status="VERIFIED",
        )
        self.db.add(self.channel)
        self.db.commit()

        self.capability = MarketplaceFulfillmentCapability(
            channel_id=self.channel.id,
            fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
            is_supported=True, requires_eligibility_check=False,
            requires_account_contract=False,
            external_display_name="판매자 배송", policy_version="1.0.0",
            schema_name="coupang_seller_fulfilled", schema_version="1.0.0",
            doc_source_reference="test", status=CapabilityStatus.VERIFIED,
            verified_at=datetime.utcnow(),
        )
        self.db.add(self.capability)
        self.db.commit()

        self.company_a = self._seed_company(
            "A", "111-11-11111", "tenant-a@example.com",
        )
        self.company_b = self._seed_company(
            "B", "222-22-22222", "tenant-b@example.com",
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    # ----------------------------------------------------
    # 시딩 헬퍼
    # ----------------------------------------------------

    def _seed_company(self, label, business_number, email):

        company = Company(
            name=f"테스트 회사 {label}", business_number=business_number,
            ceo="테스트", phone="02-000-0000", email=email, address="서울",
        )
        self.db.add(company)
        self.db.commit()

        return company

    class _Chain:
        """한 회사의 candidate/account/listing/selection 묶음."""

        def __init__(
            self, company_id, candidate, account, listing, selection,
        ):
            self.company_id = company_id
            self.candidate = candidate
            self.account = account
            self.listing = listing
            self.selection = selection

    def _build_chain(self, company_id, ref) -> "_Chain":

        candidate = ProductCandidate(
            candidate_key=f"test:COUPANG:{ref}", source_type="TREND",
            source_reference=ref, market="COUPANG",
            product_name="테스트 상품", status=CandidateStatus.APPROVED,
        )
        self.db.add(candidate)
        self.db.commit()

        account = MarketplaceAccount(
            company_id=company_id, channel_id=self.channel.id,
            account_code=f"acct-{ref}", account_name="계정",
        )
        self.db.add(account)
        self.db.commit()

        listing, _dup = self.listing_service.create_listing(
            MarketplaceListingCreateRequest(
                product_candidate_id=candidate.id,
                marketplace_account_id=account.id,
            ),
            company_id,
        )
        selection, _dup, _events = self.listing_service.select_fulfillment_mode(
            MarketplaceFulfillmentSelectionCreateRequest(
                listing_id=listing.id,
                fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                required_fields=VALID_COUPANG_SELLER_FULFILLED_FIELDS,
                idempotency_key=_next_key("sel"),
            ),
            selected_by=1, company_id=company_id,
        )

        return self._Chain(company_id, candidate, account, listing, selection)

    def _approve(self, chain, idem_prefix="appreq"):

        pending, _dup = self.approval_service.request_approval(
            MarketplaceSubmissionApprovalRequestRequest(
                listing_id=chain.listing.id, selection_id=chain.selection.id,
                planned_quantity=10, unit_price=Decimal("9000"),
                unit_cost_of_goods=Decimal("5000"),
                expected_logistics_cost=Decimal("500"),
                idempotency_key=_next_key(idem_prefix),
            ),
            requested_by=1, company_id=chain.company_id,
        )
        approved, _dup = self.approval_service.approve(
            pending.id,
            MarketplaceSubmissionApprovalDecisionRequest(
                expires_at=datetime.utcnow() + timedelta(days=1),
                idempotency_key=_next_key("appdec"),
            ),
            approved_by=99, company_id=chain.company_id,
        )

        channel = self.db.get(MarketplaceChannel, chain.account.channel_id)
        seed_channel_eligible_policy(
            self.db, company_id=chain.company_id,
            product_candidate_id=chain.candidate.id,
            channel=channel.code, selection_id=chain.selection.id,
        )

        return approved

    # ----------------------------------------------------
    # 1) Listing 조회/조작 — 전부 404
    # ----------------------------------------------------

    def test_cross_company_listing_get_returns_404(self):

        chain_b = self._build_chain(self.company_b.id, "L1")

        with self.assertRaises(NotFoundException):
            self.listing_service.get_listing(
                chain_b.listing.id, self.company_a.id,
            )

    def test_cross_company_current_selection_returns_none_not_leaked(self):

        chain_b = self._build_chain(self.company_b.id, "L2")

        result = self.listing_service.get_current_selection(
            chain_b.listing.id, self.company_a.id,
        )

        self.assertIsNone(result)

    def test_cross_company_pause_returns_404(self):

        chain_b = self._build_chain(self.company_b.id, "L3")

        with self.assertRaises(NotFoundException):
            self.listing_service.pause_listing(
                chain_b.listing.id, self.company_a.id,
            )

    def test_cross_company_resume_returns_404(self):

        chain_b = self._build_chain(self.company_b.id, "L4")
        self.listing_service.pause_listing(chain_b.listing.id, self.company_b.id)

        with self.assertRaises(NotFoundException):
            self.listing_service.resume_listing(
                chain_b.listing.id, self.company_a.id,
            )

    # ----------------------------------------------------
    # 2) Approval — 요청/승인/거절/취소 전부 404
    # ----------------------------------------------------

    def test_cross_company_request_approval_with_other_companys_listing_is_404(
        self,
    ):

        chain_b = self._build_chain(self.company_b.id, "AP1")

        with self.assertRaises(NotFoundException):
            self.approval_service.request_approval(
                MarketplaceSubmissionApprovalRequestRequest(
                    listing_id=chain_b.listing.id,
                    selection_id=chain_b.selection.id,
                    planned_quantity=10, unit_price=Decimal("9000"),
                    unit_cost_of_goods=Decimal("5000"),
                    expected_logistics_cost=Decimal("500"),
                    idempotency_key=_next_key("appreq"),
                ),
                requested_by=1, company_id=self.company_a.id,
            )

    def test_cross_company_approve_returns_404(self):

        chain_b = self._build_chain(self.company_b.id, "AP2")
        pending, _dup = self.approval_service.request_approval(
            MarketplaceSubmissionApprovalRequestRequest(
                listing_id=chain_b.listing.id, selection_id=chain_b.selection.id,
                planned_quantity=10, unit_price=Decimal("9000"),
                unit_cost_of_goods=Decimal("5000"),
                expected_logistics_cost=Decimal("500"),
                idempotency_key=_next_key("appreq"),
            ),
            requested_by=1, company_id=self.company_b.id,
        )

        with self.assertRaises(NotFoundException):
            self.approval_service.approve(
                pending.id,
                MarketplaceSubmissionApprovalDecisionRequest(
                    expires_at=datetime.utcnow() + timedelta(days=1),
                    idempotency_key=_next_key("appdec"),
                ),
                approved_by=1, company_id=self.company_a.id,
            )

    def test_cross_company_reject_returns_404(self):

        chain_b = self._build_chain(self.company_b.id, "AP3")
        pending, _dup = self.approval_service.request_approval(
            MarketplaceSubmissionApprovalRequestRequest(
                listing_id=chain_b.listing.id, selection_id=chain_b.selection.id,
                planned_quantity=10, unit_price=Decimal("9000"),
                unit_cost_of_goods=Decimal("5000"),
                expected_logistics_cost=Decimal("500"),
                idempotency_key=_next_key("appreq"),
            ),
            requested_by=1, company_id=self.company_b.id,
        )

        with self.assertRaises(NotFoundException):
            self.approval_service.reject(
                pending.id,
                MarketplaceSubmissionApprovalRejectionRequest(
                    reason="타사 시도", idempotency_key=_next_key("reject"),
                ),
                rejected_by=1, company_id=self.company_a.id,
            )

    def test_cross_company_revoke_returns_404(self):

        chain_b = self._build_chain(self.company_b.id, "AP4")
        approved = self._approve(chain_b)

        with self.assertRaises(NotFoundException):
            self.approval_service.revoke(
                approved.id,
                MarketplaceSubmissionApprovalRejectionRequest(
                    reason="타사 시도", idempotency_key=_next_key("revoke"),
                ),
                revoked_by=1, company_id=self.company_a.id,
            )

    def test_cross_company_approval_history_returns_empty_not_error(self):

        chain_b = self._build_chain(self.company_b.id, "AP5")
        self._approve(chain_b)

        history = self.approval_service.history(
            chain_b.listing.id, chain_b.selection.id, self.company_a.id,
        )

        self.assertEqual(history, [])

    # ----------------------------------------------------
    # 3) Submission — 제출/이력 전부 격리
    # ----------------------------------------------------

    def test_channel_policy_evaluation_from_other_company_cannot_be_reused(self):
        """CA-6 — 타사 채널 정책 평가 재사용 차단. company_a가 실제로
        유효한 정책 평가를 받았더라도, company_b의 (다른) listing/
        selection에는 그 평가가 절대 재사용되지 않는다 —
        current_valid_channel_policy가 company_id로 조회를 격리하기
        때문이다(company_a의 평가 행 자체가 company_b 쪽 회사ID로는
        조회되지 않는다)."""

        chain_a = self._build_chain(self.company_a.id, "CPI1")
        self._approve(chain_a)  # company_a는 정상적으로 정책 평가까지 완료됨

        chain_b = self._build_chain(self.company_b.id, "CPI2")
        # company_b는 승인만 받고 채널 정책 평가는 받지 않는다 —
        # company_a의 평가를 몰래 재사용할 수 없어야 한다.
        pending, _dup = self.approval_service.request_approval(
            MarketplaceSubmissionApprovalRequestRequest(
                listing_id=chain_b.listing.id,
                selection_id=chain_b.selection.id,
                planned_quantity=10, unit_price=Decimal("9000"),
                unit_cost_of_goods=Decimal("5000"),
                expected_logistics_cost=Decimal("500"),
                idempotency_key=_next_key("appreq"),
            ),
            requested_by=1, company_id=self.company_b.id,
        )
        self.approval_service.approve(
            pending.id,
            MarketplaceSubmissionApprovalDecisionRequest(
                expires_at=datetime.utcnow() + timedelta(days=1),
                idempotency_key=_next_key("appdec"),
            ),
            approved_by=99, company_id=self.company_b.id,
        )

        result = self.submission_service.submit(
            MarketplaceSubmissionRequest(
                listing_id=chain_b.listing.id,
                selection_id=chain_b.selection.id,
                idempotency_key=_next_key("sub"),
            ),
            self.company_b.id,
        )
        self.assertEqual(result.status, "FAILED")
        self.assertIn("CHANNEL_POLICY_GATE_FAILED", result.error_reason)

    def test_cross_company_submit_returns_404(self):

        chain_b = self._build_chain(self.company_b.id, "SB1")
        self._approve(chain_b)

        with self.assertRaises(NotFoundException):
            self.submission_service.submit(
                MarketplaceSubmissionRequest(
                    listing_id=chain_b.listing.id,
                    selection_id=chain_b.selection.id,
                    idempotency_key=_next_key("sub"),
                ),
                self.company_a.id,
            )

    def test_cross_company_submission_history_returns_empty_not_error(self):

        chain_b = self._build_chain(self.company_b.id, "SB2")
        approved = self._approve(chain_b)
        self.submission_service.submit(
            MarketplaceSubmissionRequest(
                listing_id=chain_b.listing.id,
                selection_id=chain_b.selection.id,
                idempotency_key=_next_key("sub"),
            ),
            chain_b.company_id,
        )
        self.assertIsNotNone(approved)

        history = self.submission_service.list_submissions(
            chain_b.listing.id, self.company_a.id,
        )

        self.assertEqual(history, [])

    # ----------------------------------------------------
    # 4) Candidate summary — 다른 회사 행이 섞이지 않는다
    # ----------------------------------------------------

    def test_candidate_summary_never_includes_other_companys_listing(self):

        candidate = ProductCandidate(
            candidate_key="test:COUPANG:SHARED-1", source_type="TREND",
            source_reference="SHARED-1", market="COUPANG",
            product_name="공용 후보", status=CandidateStatus.APPROVED,
        )
        self.db.add(candidate)
        self.db.commit()

        account_a = MarketplaceAccount(
            company_id=self.company_a.id, channel_id=self.channel.id,
            account_code="shared-a", account_name="A계정",
        )
        account_b = MarketplaceAccount(
            company_id=self.company_b.id, channel_id=self.channel.id,
            account_code="shared-b", account_name="B계정",
        )
        self.db.add_all([account_a, account_b])
        self.db.commit()

        self.listing_service.create_listing(
            MarketplaceListingCreateRequest(
                product_candidate_id=candidate.id,
                marketplace_account_id=account_a.id,
            ),
            self.company_a.id,
        )
        self.listing_service.create_listing(
            MarketplaceListingCreateRequest(
                product_candidate_id=candidate.id,
                marketplace_account_id=account_b.id,
            ),
            self.company_b.id,
        )

        summary_a = self.listing_service.channel_selections_summary(
            candidate.id, self.company_a.id,
        )
        summary_b = self.listing_service.channel_selections_summary(
            candidate.id, self.company_b.id,
        )

        self.assertEqual(len(summary_a), 1)
        self.assertEqual(len(summary_b), 1)
        self.assertEqual(summary_a[0]["account_name"], "A계정")
        self.assertEqual(summary_b[0]["account_name"], "B계정")

    # ----------------------------------------------------
    # 5) Eligibility — 회사 격리
    # ----------------------------------------------------

    def test_cross_company_eligibility_history_returns_empty_not_error(self):

        chain_b = self._build_chain(self.company_b.id, "EL1")

        history = self.eligibility_service.history(
            chain_b.account.id, FulfillmentMode.SELLER_FULFILLED,
            self.company_a.id,
        )

        self.assertEqual(history, [])

    # ----------------------------------------------------
    # 6) 실패 후 타사 상태·row 불변
    # ----------------------------------------------------

    def test_failed_cross_company_pause_leaves_target_listing_status_unchanged(
        self,
    ):

        chain_b = self._build_chain(self.company_b.id, "ST1")
        before = self.listing_service.get_listing(
            chain_b.listing.id, self.company_b.id,
        ).status

        with self.assertRaises(NotFoundException):
            self.listing_service.pause_listing(
                chain_b.listing.id, self.company_a.id,
            )

        after = self.listing_service.get_listing(
            chain_b.listing.id, self.company_b.id,
        ).status

        self.assertEqual(before, after)

    def test_failed_cross_company_approve_leaves_approval_table_row_count_unchanged(
        self,
    ):

        chain_b = self._build_chain(self.company_b.id, "ST2")
        pending, _dup = self.approval_service.request_approval(
            MarketplaceSubmissionApprovalRequestRequest(
                listing_id=chain_b.listing.id, selection_id=chain_b.selection.id,
                planned_quantity=10, unit_price=Decimal("9000"),
                unit_cost_of_goods=Decimal("5000"),
                expected_logistics_cost=Decimal("500"),
                idempotency_key=_next_key("appreq"),
            ),
            requested_by=1, company_id=self.company_b.id,
        )

        before_count = (
            self.db.query(MarketplaceSubmissionApproval)
            .filter(MarketplaceSubmissionApproval.listing_id == chain_b.listing.id)
            .count()
        )

        with self.assertRaises(NotFoundException):
            self.approval_service.approve(
                pending.id,
                MarketplaceSubmissionApprovalDecisionRequest(
                    expires_at=datetime.utcnow() + timedelta(days=1),
                    idempotency_key=_next_key("appdec"),
                ),
                approved_by=1, company_id=self.company_a.id,
            )

        after_count = (
            self.db.query(MarketplaceSubmissionApproval)
            .filter(MarketplaceSubmissionApproval.listing_id == chain_b.listing.id)
            .count()
        )

        self.assertEqual(before_count, after_count)
        self.assertEqual(
            pending.status, ApprovalStatus.PENDING,
            "타사 승인 시도 실패 후에도 원래 PENDING 행 자체는 변경되지 않아야 한다.",
        )

    def test_failed_cross_company_submit_leaves_submission_table_row_count_unchanged(
        self,
    ):

        chain_b = self._build_chain(self.company_b.id, "ST3")
        self._approve(chain_b)

        before_count = (
            self.db.query(MarketplaceSubmission)
            .filter(MarketplaceSubmission.listing_id == chain_b.listing.id)
            .count()
        )

        with self.assertRaises(NotFoundException):
            self.submission_service.submit(
                MarketplaceSubmissionRequest(
                    listing_id=chain_b.listing.id,
                    selection_id=chain_b.selection.id,
                    idempotency_key=_next_key("sub"),
                ),
                self.company_a.id,
            )

        after_count = (
            self.db.query(MarketplaceSubmission)
            .filter(MarketplaceSubmission.listing_id == chain_b.listing.id)
            .count()
        )

        self.assertEqual(before_count, after_count)

    # ----------------------------------------------------
    # 7) idempotency_key — 회사 A/B가 독립적으로 재사용 가능
    # ----------------------------------------------------

    def test_same_idempotency_key_usable_independently_by_both_companies(self):

        shared_key = "shared-idem-key-across-companies"

        candidate_a = ProductCandidate(
            candidate_key="test:COUPANG:IDEM-A", source_type="TREND",
            source_reference="IDEM-A", market="COUPANG",
            product_name="A 상품", status=CandidateStatus.APPROVED,
        )
        candidate_b = ProductCandidate(
            candidate_key="test:COUPANG:IDEM-B", source_type="TREND",
            source_reference="IDEM-B", market="COUPANG",
            product_name="B 상품", status=CandidateStatus.APPROVED,
        )
        self.db.add_all([candidate_a, candidate_b])
        self.db.commit()

        account_a = MarketplaceAccount(
            company_id=self.company_a.id, channel_id=self.channel.id,
            account_code="idem-a", account_name="A계정",
        )
        account_b = MarketplaceAccount(
            company_id=self.company_b.id, channel_id=self.channel.id,
            account_code="idem-b", account_name="B계정",
        )
        self.db.add_all([account_a, account_b])
        self.db.commit()

        listing_a, _dup = self.listing_service.create_listing(
            MarketplaceListingCreateRequest(
                product_candidate_id=candidate_a.id,
                marketplace_account_id=account_a.id,
            ),
            self.company_a.id,
        )
        listing_b, _dup = self.listing_service.create_listing(
            MarketplaceListingCreateRequest(
                product_candidate_id=candidate_b.id,
                marketplace_account_id=account_b.id,
            ),
            self.company_b.id,
        )

        selection_a, _dup, _events = self.listing_service.select_fulfillment_mode(
            MarketplaceFulfillmentSelectionCreateRequest(
                listing_id=listing_a.id,
                fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                required_fields=VALID_COUPANG_SELLER_FULFILLED_FIELDS,
                idempotency_key=shared_key,
            ),
            selected_by=1, company_id=self.company_a.id,
        )
        selection_b, _dup, _events = self.listing_service.select_fulfillment_mode(
            MarketplaceFulfillmentSelectionCreateRequest(
                listing_id=listing_b.id,
                fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                required_fields=VALID_COUPANG_SELLER_FULFILLED_FIELDS,
                idempotency_key=shared_key,
            ),
            selected_by=1, company_id=self.company_b.id,
        )

        self.assertNotEqual(selection_a.id, selection_b.id)
        self.assertEqual(selection_a.idempotency_key, shared_key)
        self.assertEqual(selection_b.idempotency_key, shared_key)

        rows = (
            self.db.query(MarketplaceFulfillmentSelection)
            .filter(MarketplaceFulfillmentSelection.idempotency_key == shared_key)
            .all()
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {row.company_id for row in rows},
            {self.company_a.id, self.company_b.id},
        )

    # ----------------------------------------------------
    # 8) 동시성 — 회사 경계가 동시 접근에서도 유지된다
    # ----------------------------------------------------

    def _threaded_engine(self):

        from sqlalchemy import event

        engine2 = create_engine(
            f"sqlite:///{self.db_path}", connect_args={"timeout": 15},
        )

        @event.listens_for(engine2, "connect")
        def _set_busy_timeout(dbapi_connection, connection_record):

            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()

        return engine2, sessionmaker(
            autocommit=False, autoflush=False, bind=engine2,
        )

    def test_concurrent_pause_across_companies_stays_isolated(self):
        """
        회사 A/B가 각자 자신의 listing을 동시에 pause해도, 서로의
        listing에는 절대 영향을 주지 않는다(같은 listing에 대한
        idempotency_key 경쟁이 아니라, 서로 다른 listing에 대한
        동시 접근이 경계를 넘지 않는지를 검증한다).
        """

        chain_a = self._build_chain(self.company_a.id, "CC1")
        chain_b = self._build_chain(self.company_b.id, "CC2")

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            results = {}
            barrier = threading.Barrier(2)

            def worker(name, listing_id, company_id):

                thread_db = SessionLocal2()
                service = MarketplaceListingService(thread_db)

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    listing = service.pause_listing(listing_id, company_id)
                    results[name] = listing.status
                except Exception as e:  # noqa: BLE001
                    results[name] = f"error:{type(e).__name__}"
                finally:
                    thread_db.close()

            t1 = threading.Thread(
                target=worker,
                args=("company-a", chain_a.listing.id, self.company_a.id),
            )
            t2 = threading.Thread(
                target=worker,
                args=("company-b", chain_b.listing.id, self.company_b.id),
            )
            t1.start()
            t2.start()
            t1.join(timeout=15)
            t2.join(timeout=15)

            self.assertEqual(results.get("company-a"), "PAUSED")
            self.assertEqual(results.get("company-b"), "PAUSED")

            verify_db = SessionLocal2()
            try:
                verify_service = MarketplaceListingService(verify_db)
                listing_a = verify_service.get_listing(
                    chain_a.listing.id, self.company_a.id,
                )
                listing_b = verify_service.get_listing(
                    chain_b.listing.id, self.company_b.id,
                )
                self.assertEqual(listing_a.status, "PAUSED")
                self.assertEqual(listing_b.status, "PAUSED")

                with self.assertRaises(NotFoundException):
                    verify_service.get_listing(
                        chain_a.listing.id, self.company_b.id,
                    )
                with self.assertRaises(NotFoundException):
                    verify_service.get_listing(
                        chain_b.listing.id, self.company_a.id,
                    )
            finally:
                verify_db.close()

        finally:
            engine2.dispose()

    # ----------------------------------------------------
    # 9) 오류 메시지가 타사 존재 여부를 노출하지 않는다
    # ----------------------------------------------------

    def test_cross_company_and_truly_nonexistent_produce_identical_error_message(
        self,
    ):
        """
        타사 소유 listing_id(999999가 아니라 실제로 존재하지만 다른
        회사 소유)와, 아예 존재하지 않는 listing_id(999999999)가 완전히
        동일한 예외 메시지를 내야 한다 — 응답만으로 "이 id는 존재하지만
        내 회사 것이 아니다"를 구분할 수 없어야 한다.
        """

        chain_b = self._build_chain(self.company_b.id, "MSG1")

        with self.assertRaises(NotFoundException) as cross_ctx:
            self.listing_service.get_listing(
                chain_b.listing.id, self.company_a.id,
            )

        with self.assertRaises(NotFoundException) as truly_missing_ctx:
            self.listing_service.get_listing(999999999, self.company_a.id)

        self.assertEqual(
            cross_ctx.exception.detail, truly_missing_ctx.exception.detail,
        )


if __name__ == "__main__":
    unittest.main()
