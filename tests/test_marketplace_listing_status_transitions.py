"""
=========================================================
Homez OS

File : tests/test_marketplace_listing_status_transitions.py

Gate 5(2026-08-01, HOMEZ Phase 1 이후 Gate 1~5, CTO 2~3차 지적 반영) —
ListingStatus(DRAFT/READY/SUBMITTING/SUBMITTED/APPROVED/REJECTED/
FAILED/PAUSED) 상태 전이 검증.

수정 전 결함: repository.update_listing_status_conditional()가
정의만 되어 있고 service.py 어디서도 호출되지 않아, 모든 Listing이
생성 시점 "DRAFT"에 영원히 고정돼 있었다 — "플랫폼별 등록 상태"
화면이 실제 진행 상황을 전혀 반영하지 못했다.

이 상태 전이는 순수 표시/감사용이다 — 실제 제출 인가는 여전히
ApprovalService.current_valid_approval()(fingerprint/만료/정책버전)
뿐이다. 이 테스트 파일은 그 분리가 실제로 유지되는지도 함께
검증한다(listing.status가 바뀌어도 이미 발급된 유효한 승인이
스스로를 무효화하지 않아야 한다 — 이전에 fingerprint에 status가
포함돼 있어 발생했던 순환 무효화 버그의 회귀 방지).

2026-08-01 CTO 3차 지적 반영 — 모든 서비스 호출에 company_id를
명시적으로 전달한다(회사 소유권 격리가 필수 인자가 되었다).
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

from app.core.exceptions import BadRequestException, NotFoundException
from app.database.base import Base
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.model import (
    AutomationModeState,
    EmergencyStop,
    ExecutionLimit,
    ExecutionPeriodUsage,
    ExecutionUsage,
)
from app.domains.automation_safety.service import SafetyService
from app.domains.company.model import Company
from app.domains.channel_policy.model import ChannelPolicyEvaluation
from app.domains.channel_policy.model import ChannelPolicyRule
from app.domains.channel_policy.model import CompanyChannelPolicySettings
from app.domains.media_asset.model import MediaAsset
from app.domains.marketplace_listing.approval_service import ApprovalService
from app.domains.marketplace_listing.constants import (
    CapabilityStatus,
    FulfillmentMode,
    ListingStatus,
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
    MarketplaceSubmissionApprovalRejectionRequest,
    MarketplaceSubmissionApprovalRequestRequest,
    MarketplaceSubmissionRequest,
)
from app.domains.marketplace_listing.service import MarketplaceListingService
from app.domains.marketplace_listing.submission_service import SubmissionService
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


class ListingStatusTransitionTestCase(unittest.TestCase):

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
            name="테스트 회사", business_number="111-11-11111", ceo="테스트",
            phone="02-000-0000", email="lst@example.com", address="서울",
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

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _allow_automation(self):

        self.safety_service.set_mode(
            AutomationMode.OPERATOR_APPROVAL, set_by=1, is_admin=True,
        )

    def _candidate(self, ref="CAND-1") -> ProductCandidate:

        candidate = ProductCandidate(
            candidate_key=f"test:COUPANG:{ref}", source_type="TREND",
            source_reference=ref, market="COUPANG",
            product_name="테스트 상품", status=CandidateStatus.APPROVED,
        )
        self.db.add(candidate)
        self.db.commit()

        return candidate

    def _channel(self, code="COUPANG", name="쿠팡") -> MarketplaceChannel:

        channel = MarketplaceChannel(
            code=code, name=name, doc_verification_status="VERIFIED",
        )
        self.db.add(channel)
        self.db.commit()

        return channel

    def _account(self, channel_id: int, code="a1") -> MarketplaceAccount:

        account = MarketplaceAccount(
            company_id=self.company_id, channel_id=channel_id,
            account_code=code, account_name="계정",
        )
        self.db.add(account)
        self.db.commit()

        return account

    def _capability(
        self, channel_id: int, mode=FulfillmentMode.SELLER_FULFILLED,
        schema_name="coupang_seller_fulfilled", schema_version="1.0.0",
    ) -> MarketplaceFulfillmentCapability:

        capability = MarketplaceFulfillmentCapability(
            channel_id=channel_id, fulfillment_mode=mode, is_supported=True,
            requires_eligibility_check=False, requires_account_contract=False,
            external_display_name=mode, policy_version="1.0.0",
            schema_name=schema_name, schema_version=schema_version,
            doc_source_reference="test", status=CapabilityStatus.VERIFIED,
            verified_at=datetime.utcnow(),
        )
        self.db.add(capability)
        self.db.commit()

        return capability

    def _listing_with_selection(self, candidate_id, account_id):

        listing, _dup = self.listing_service.create_listing(
            MarketplaceListingCreateRequest(
                product_candidate_id=candidate_id,
                marketplace_account_id=account_id,
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

        return listing, selection

    def _request_approval(self, listing, selection, **overrides):

        params = dict(
            planned_quantity=10, unit_price=Decimal("9000"),
            unit_cost_of_goods=Decimal("5000"),
            expected_logistics_cost=Decimal("500"),
        )
        params.update(overrides)

        pending, _dup = self.approval_service.request_approval(
            MarketplaceSubmissionApprovalRequestRequest(
                listing_id=listing.id, selection_id=selection.id,
                idempotency_key=_next_key("appreq"), **params,
            ),
            requested_by=1, company_id=self.company_id,
        )

        return pending

    def _approve(self, listing, selection, expires_at=None):

        pending = self._request_approval(listing, selection)
        approved, _dup = self.approval_service.approve(
            pending.id,
            MarketplaceSubmissionApprovalDecisionRequest(
                expires_at=expires_at or (
                    datetime.utcnow() + timedelta(days=1)
                ),
                idempotency_key=_next_key("appdec"),
            ),
            approved_by=99, company_id=self.company_id,
        )

        account = self.db.get(MarketplaceAccount, listing.marketplace_account_id)
        channel = self.db.get(MarketplaceChannel, account.channel_id)
        seed_channel_eligible_policy(
            self.db, company_id=self.company_id,
            product_candidate_id=listing.product_candidate_id,
            channel=channel.code, selection_id=selection.id,
        )

        return approved

    def _setup_ready_listing(self):

        self._allow_automation()
        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)

        return self._listing_with_selection(candidate.id, account.id)

    # ----------------------------------------------------
    # 1) select_fulfillment_mode: DRAFT → READY
    # ----------------------------------------------------

    def test_selecting_fulfillment_mode_transitions_draft_to_ready(self):

        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)

        listing, _dup = self.listing_service.create_listing(
            MarketplaceListingCreateRequest(
                product_candidate_id=candidate.id,
                marketplace_account_id=account.id,
            ),
            self.company_id,
        )
        self.assertEqual(listing.status, ListingStatus.DRAFT)

        self.listing_service.select_fulfillment_mode(
            MarketplaceFulfillmentSelectionCreateRequest(
                listing_id=listing.id,
                fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                required_fields=VALID_COUPANG_SELLER_FULFILLED_FIELDS,
                idempotency_key=_next_key("sel"),
            ),
            selected_by=1, company_id=self.company_id,
        )

        refreshed = self.listing_service.get_listing(listing.id, self.company_id)
        self.assertEqual(refreshed.status, ListingStatus.READY)

    # ----------------------------------------------------
    # 2) approve(): READY → APPROVED
    # ----------------------------------------------------

    def test_approve_transitions_ready_to_approved(self):

        listing, selection = self._setup_ready_listing()
        self.assertEqual(
            self.listing_service.get_listing(listing.id, self.company_id).status,
            ListingStatus.READY,
        )

        self._approve(listing, selection)

        refreshed = self.listing_service.get_listing(listing.id, self.company_id)
        self.assertEqual(refreshed.status, ListingStatus.APPROVED)

    def test_approval_stays_valid_after_listing_status_changes(self):
        """
        회귀 방지: listing.status가 READY→APPROVED로 바뀌어도, 방금
        발급된 그 승인 자체가 fingerprint 불일치로 스스로 무효화되면
        안 된다(fingerprint에서 status를 제외한 수정의 핵심 검증).
        """

        listing, selection = self._setup_ready_listing()
        approval = self._approve(listing, selection)

        valid = self.approval_service.current_valid_approval(
            listing.id, selection.id, self.company_id,
        )
        self.assertIsNotNone(valid)
        self.assertEqual(valid.id, approval.id)

    # ----------------------------------------------------
    # 3) reject(): READY → REJECTED
    # ----------------------------------------------------

    def test_reject_transitions_ready_to_rejected(self):

        listing, selection = self._setup_ready_listing()
        pending = self._request_approval(listing, selection)

        self.approval_service.reject(
            pending.id,
            MarketplaceSubmissionApprovalRejectionRequest(
                reason="가격 재검토 필요", idempotency_key=_next_key("rej"),
            ),
            rejected_by=99, company_id=self.company_id,
        )

        refreshed = self.listing_service.get_listing(listing.id, self.company_id)
        self.assertEqual(refreshed.status, ListingStatus.REJECTED)

    # ----------------------------------------------------
    # 4) revoke(): APPROVED → READY
    # ----------------------------------------------------

    def test_revoke_transitions_approved_back_to_ready(self):

        listing, selection = self._setup_ready_listing()
        approval = self._approve(listing, selection)
        self.assertEqual(
            self.listing_service.get_listing(listing.id, self.company_id).status,
            ListingStatus.APPROVED,
        )

        self.approval_service.revoke(
            approval.id,
            MarketplaceSubmissionApprovalRejectionRequest(
                reason="계획 변경", idempotency_key=_next_key("rev"),
            ),
            revoked_by=99, company_id=self.company_id,
        )

        refreshed = self.listing_service.get_listing(listing.id, self.company_id)
        self.assertEqual(refreshed.status, ListingStatus.READY)

    # ----------------------------------------------------
    # 5) pause_listing() / resume_listing()
    # ----------------------------------------------------

    def test_pause_from_ready_succeeds(self):

        listing, _selection = self._setup_ready_listing()

        paused = self.listing_service.pause_listing(listing.id, self.company_id)

        self.assertEqual(paused.status, ListingStatus.PAUSED)

    def test_pause_from_approved_succeeds(self):

        listing, selection = self._setup_ready_listing()
        self._approve(listing, selection)

        paused = self.listing_service.pause_listing(listing.id, self.company_id)

        self.assertEqual(paused.status, ListingStatus.PAUSED)

    def test_pause_from_draft_is_rejected(self):

        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)

        listing, _dup = self.listing_service.create_listing(
            MarketplaceListingCreateRequest(
                product_candidate_id=candidate.id,
                marketplace_account_id=account.id,
            ),
            self.company_id,
        )

        with self.assertRaises(BadRequestException):
            self.listing_service.pause_listing(listing.id, self.company_id)

    def test_pause_is_idempotent_on_repeated_call(self):

        listing, _selection = self._setup_ready_listing()

        first = self.listing_service.pause_listing(listing.id, self.company_id)
        second = self.listing_service.pause_listing(listing.id, self.company_id)

        self.assertEqual(first.status, ListingStatus.PAUSED)
        self.assertEqual(second.status, ListingStatus.PAUSED)

    def test_pause_nonexistent_listing_raises_not_found(self):

        with self.assertRaises(NotFoundException):
            self.listing_service.pause_listing(999999, self.company_id)

    def test_resume_from_paused_returns_to_ready(self):

        listing, _selection = self._setup_ready_listing()
        self.listing_service.pause_listing(listing.id, self.company_id)

        resumed = self.listing_service.resume_listing(listing.id, self.company_id)

        self.assertEqual(resumed.status, ListingStatus.READY)

    def test_resume_from_non_paused_is_rejected(self):

        listing, selection = self._setup_ready_listing()
        self._approve(listing, selection)  # READY -> APPROVED, not PAUSED

        with self.assertRaises(BadRequestException):
            self.listing_service.resume_listing(listing.id, self.company_id)

    def test_resume_is_idempotent_on_repeated_call(self):

        listing, _selection = self._setup_ready_listing()
        self.listing_service.pause_listing(listing.id, self.company_id)

        first = self.listing_service.resume_listing(listing.id, self.company_id)
        second = self.listing_service.resume_listing(listing.id, self.company_id)

        self.assertEqual(first.status, ListingStatus.READY)
        self.assertEqual(second.status, ListingStatus.READY)

    def test_pausing_does_not_bypass_real_submission_gate(self):
        """
        listing.status는 순수 표시용이다 — PAUSED로 바꿔도 실제 제출
        인가(ApprovalService.current_valid_approval)는 전혀 영향받지
        않는다(그 반대도 마찬가지: 이 필드를 조작해도 보안 게이트를
        우회할 수 없다는 것을 확인한다).
        """

        listing, selection = self._setup_ready_listing()
        approval = self._approve(listing, selection)
        self.listing_service.pause_listing(listing.id, self.company_id)

        # PAUSED 상태에서도 실제 승인 유효성 판단은 fingerprint 기준
        # 그대로다 — listing.status와 무관.
        valid = self.approval_service.current_valid_approval(
            listing.id, selection.id, self.company_id,
        )
        self.assertIsNotNone(valid)
        self.assertEqual(valid.id, approval.id)

    # ----------------------------------------------------
    # 6) submit(): APPROVED → SUBMITTING → SUBMITTED/FAILED/(그대로)
    # ----------------------------------------------------

    def test_submit_success_transitions_through_submitting_to_submitted(self):

        listing, selection = self._setup_ready_listing()
        self._approve(listing, selection)

        result = self.submission_service.submit(
            MarketplaceSubmissionRequest(
                listing_id=listing.id, selection_id=selection.id,
                idempotency_key=_next_key("sub"),
            ),
            self.company_id,
        )

        self.assertEqual(result.status, "PENDING")

        refreshed = self.listing_service.get_listing(listing.id, self.company_id)
        self.assertEqual(refreshed.status, ListingStatus.SUBMITTED)

    def test_submit_failure_after_submitting_transitions_to_failed(self):

        listing, selection = self._setup_ready_listing()
        self._approve(listing, selection)

        # 저장소 손상을 직접 재현 — fail-closed 실패 경로를 유도한다.
        selection.required_fields_json = "{not-valid-json::"
        self.db.commit()

        # required_fields_json 변경은 채널 정책 지문도 무효화한다
        # (CA-1 설계 — 옵션/가격/배송 변경 감지가 정확히 이 필드를
        # 통해 이뤄진다). 이 테스트가 실제로 검증하려는 것은 더 깊은
        # required_fields Schema 재검증 실패 경로이므로, 손상된 값을
        # 그대로 반영해 정책 게이트만 다시 통과시킨다.
        account = self.db.get(MarketplaceAccount, listing.marketplace_account_id)
        channel = self.db.get(MarketplaceChannel, account.channel_id)
        seed_channel_eligible_policy(
            self.db, company_id=self.company_id,
            product_candidate_id=listing.product_candidate_id,
            channel=channel.code, selection_id=selection.id,
        )

        result = self.submission_service.submit(
            MarketplaceSubmissionRequest(
                listing_id=listing.id, selection_id=selection.id,
                idempotency_key=_next_key("sub"),
            ),
            self.company_id,
        )

        self.assertEqual(result.status, "FAILED")

        refreshed = self.listing_service.get_listing(listing.id, self.company_id)
        self.assertEqual(refreshed.status, ListingStatus.FAILED)

    def test_submit_unknown_outcome_leaves_listing_in_submitting(self):
        """
        어댑터 예외처럼 성공/실패를 추론할 수 없는 경우(UNKNOWN)는
        listing.status를 강제로 SUBMITTED/FAILED로 밀어넣지 않는다 —
        SUBMITTING에 그대로 남겨 사람이 확인해야 함을 표시한다.
        """

        listing, selection = self._setup_ready_listing()
        self._approve(listing, selection)

        with mock.patch(
            "app.domains.marketplace_listing.adapters.coupang_adapter."
            "CoupangFulfillmentAdapter.translate",
            side_effect=TimeoutError("simulated timeout"),
        ):
            result = self.submission_service.submit(
                MarketplaceSubmissionRequest(
                    listing_id=listing.id, selection_id=selection.id,
                    idempotency_key=_next_key("sub"),
                ),
                self.company_id,
            )

        self.assertEqual(result.status, "UNKNOWN")

        refreshed = self.listing_service.get_listing(listing.id, self.company_id)
        self.assertEqual(refreshed.status, ListingStatus.SUBMITTING)

    def test_submitted_status_never_implies_live_marketplace_publish(self):
        """
        이 도메인은 실제 네트워크 호출 코드 자체가 없다 — SUBMITTED가
        "우리 시스템 검증 통과"일 뿐 실제 마켓 공개가 아님을, submit()
        결과에 external_submission_ref가 채워지지 않는 것으로도
        확인한다(실제로 채널에 제출됐다면 그 참조값이 있어야 한다).
        """

        listing, selection = self._setup_ready_listing()
        self._approve(listing, selection)

        result = self.submission_service.submit(
            MarketplaceSubmissionRequest(
                listing_id=listing.id, selection_id=selection.id,
                idempotency_key=_next_key("sub"),
            ),
            self.company_id,
        )

        self.assertIsNone(result.external_submission_ref)


if __name__ == "__main__":
    unittest.main()
