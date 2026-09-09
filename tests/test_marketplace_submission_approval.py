"""
=========================================================
Homez OS

File : tests/test_marketplace_submission_approval.py

제출 승인 재감사 신규 테스트 (2026-07-31):
1) client Boolean 단독으로는 승인될 수 없다
2) 만료/취소/fingerprint 불일치 승인은 거부된다
3) 승인과 제출은 분리된 별도 요청이다
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
from app.core.exceptions import ConflictException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.marketplace_listing.approval_service import ApprovalService
from app.domains.marketplace_listing.constants import ApprovalStatus
from app.domains.marketplace_listing.constants import CapabilityStatus
from app.domains.marketplace_listing.constants import FulfillmentMode
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
from app.domains.marketplace_listing.service import MarketplaceListingService
from app.domains.marketplace_listing.submission_service import (
    SubmissionService,
)
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
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


class MarketplaceSubmissionApprovalTestCase(unittest.TestCase):

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
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        company = Company(
            name="테스트 회사", business_number="444-44-44444", ceo="테스트",
            phone="02-000-0000", email="approval@example.com", address="서울",
        )
        self.db.add(company)
        self.db.commit()
        self.company_id = company.id

        self.listing_service = MarketplaceListingService(self.db)
        self.approval_service = ApprovalService(self.db)

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
        policy_version="1.0.0",
    ) -> MarketplaceFulfillmentCapability:

        capability = MarketplaceFulfillmentCapability(
            channel_id=channel_id, fulfillment_mode=mode, is_supported=True,
            requires_eligibility_check=False, requires_account_contract=False,
            external_display_name=mode, policy_version=policy_version,
            schema_name="coupang_seller_fulfilled", schema_version="1.0.0",
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

    def _request_approval(
        self, listing, selection, planned_quantity=10,
    ) -> MarketplaceSubmissionApproval:

        pending, _dup = self.approval_service.request_approval(
            MarketplaceSubmissionApprovalRequestRequest(
                listing_id=listing.id, selection_id=selection.id,
                planned_quantity=planned_quantity, unit_price=Decimal("9000"),
                unit_cost_of_goods=Decimal("5000"),
                expected_logistics_cost=Decimal("500"),
                idempotency_key=_next_key("appreq"),
            ),
            requested_by=1, company_id=self.company_id,
        )

        return pending

    # ----------------------------------------------------
    # 1) client Boolean 단독으로는 승인될 수 없다
    # ----------------------------------------------------

    def test_client_cannot_self_approve_via_request_schema(self):
        """
        MarketplaceSubmissionApprovalRequestRequest에는 승인 여부를
        나타내는 필드가 아예 존재하지 않는다 — 요청 자체는 항상
        PENDING만 만들 수 있다는 것을 스키마·서비스 양쪽에서 확인한다.
        """

        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)
        listing, selection = self._listing_with_selection(
            candidate.id, account.id,
        )

        self.assertNotIn(
            "operator_approved",
            MarketplaceSubmissionApprovalRequestRequest.model_fields,
        )
        self.assertNotIn(
            "approved",
            MarketplaceSubmissionApprovalRequestRequest.model_fields,
        )

        pending = self._request_approval(listing, selection)

        self.assertEqual(pending.status, ApprovalStatus.PENDING)
        self.assertIsNone(pending.approved_by)

        # request_approval 시점엔 아직 유효한 승인이 아니다.
        self.assertIsNone(
            self.approval_service.current_valid_approval(
                listing.id, selection.id, self.company_id,
            ),
        )

    def test_approved_by_always_comes_from_admin_guard_caller_not_body(self):
        """
        approve()의 approved_by는 오직 메서드 파라미터에서만 온다 —
        MarketplaceSubmissionApprovalDecisionRequest에는 그런 필드가
        없다는 것과, approve() 호출자 id가 그대로 저장된다는 것을
        확인한다.
        """

        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)
        listing, selection = self._listing_with_selection(
            candidate.id, account.id,
        )
        pending = self._request_approval(listing, selection)

        self.assertNotIn(
            "approved_by",
            MarketplaceSubmissionApprovalDecisionRequest.model_fields,
        )

        approved, _dup = self.approval_service.approve(
            pending.id,
            MarketplaceSubmissionApprovalDecisionRequest(
                expires_at=datetime.utcnow() + timedelta(days=1),
                idempotency_key=_next_key("appdec"),
            ),
            approved_by=42, company_id=self.company_id,
        )

        self.assertEqual(approved.status, ApprovalStatus.APPROVED)
        self.assertEqual(approved.approved_by, 42)

    # ----------------------------------------------------
    # 2) 만료/취소/fingerprint 불일치 승인은 거부된다
    # ----------------------------------------------------

    def test_expired_approval_is_rejected_as_invalid(self):

        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)
        listing, selection = self._listing_with_selection(
            candidate.id, account.id,
        )
        pending = self._request_approval(listing, selection)

        self.approval_service.approve(
            pending.id,
            MarketplaceSubmissionApprovalDecisionRequest(
                expires_at=datetime.utcnow() - timedelta(seconds=1),
                idempotency_key=_next_key("appdec"),
            ),
            approved_by=42, company_id=self.company_id,
        )

        self.assertIsNone(
            self.approval_service.current_valid_approval(
                listing.id, selection.id, self.company_id,
            ),
        )

    def test_revoked_approval_is_rejected_as_invalid(self):

        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)
        listing, selection = self._listing_with_selection(
            candidate.id, account.id,
        )
        pending = self._request_approval(listing, selection)
        approved, _dup = self.approval_service.approve(
            pending.id,
            MarketplaceSubmissionApprovalDecisionRequest(
                expires_at=datetime.utcnow() + timedelta(days=1),
                idempotency_key=_next_key("appdec"),
            ),
            approved_by=42, company_id=self.company_id,
        )

        self.assertIsNotNone(
            self.approval_service.current_valid_approval(
                listing.id, selection.id, self.company_id,
            ),
        )

        self.approval_service.revoke(
            approved.id,
            MarketplaceSubmissionApprovalRejectionRequest(
                reason="테스트 취소", idempotency_key=_next_key("revoke"),
            ),
            revoked_by=1, company_id=self.company_id,
        )

        self.assertIsNone(
            self.approval_service.current_valid_approval(
                listing.id, selection.id, self.company_id,
            ),
        )

    def test_rejected_approval_cannot_be_approved_again(self):

        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)
        listing, selection = self._listing_with_selection(
            candidate.id, account.id,
        )
        pending = self._request_approval(listing, selection)

        rejected, _dup = self.approval_service.reject(
            pending.id,
            MarketplaceSubmissionApprovalRejectionRequest(
                reason="테스트 거절", idempotency_key=_next_key("reject"),
            ),
            rejected_by=1, company_id=self.company_id,
        )
        self.assertEqual(rejected.status, ApprovalStatus.REJECTED)

        with self.assertRaises(BadRequestException):
            self.approval_service.approve(
                rejected.id,
                MarketplaceSubmissionApprovalDecisionRequest(
                    expires_at=datetime.utcnow() + timedelta(days=1),
                    idempotency_key=_next_key("appdec"),
                ),
                approved_by=42, company_id=self.company_id,
            )

    def test_selection_change_invalidates_existing_approval_via_fingerprint(
        self,
    ):
        """
        선택(가격/수량/필수필드에 해당하는 fulfillment mode 재선택)이
        바뀌면 새 selection.id/fingerprint가 생겨, 이전 APPROVED
        승인은 자동으로 무효화되어야 한다(명시적 무효화 코드 없이).
        """

        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)
        listing, selection = self._listing_with_selection(
            candidate.id, account.id,
        )
        pending = self._request_approval(listing, selection)
        self.approval_service.approve(
            pending.id,
            MarketplaceSubmissionApprovalDecisionRequest(
                expires_at=datetime.utcnow() + timedelta(days=1),
                idempotency_key=_next_key("appdec"),
            ),
            approved_by=42, company_id=self.company_id,
        )

        self.assertIsNotNone(
            self.approval_service.current_valid_approval(
                listing.id, selection.id, self.company_id,
            ),
        )

        # 방식을 다시 선택 — 새 selection이 생성되고 이전 selection은
        # SUPERSEDED된다.
        new_fields = dict(VALID_COUPANG_SELLER_FULFILLED_FIELDS)
        new_fields["maximumBuyCount"] = 999
        new_selection, _dup, _events = (
            self.listing_service.select_fulfillment_mode(
                MarketplaceFulfillmentSelectionCreateRequest(
                    listing_id=listing.id,
                    fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                    required_fields=new_fields,
                    idempotency_key=_next_key("sel"),
                ),
                selected_by=1, company_id=self.company_id,
            )
        )
        self.assertNotEqual(new_selection.id, selection.id)

        # 이전 selection_id 기준으로는 여전히 최신 행이 APPROVED이지만,
        # selection 자체가 SUPERSEDED로 바뀌어 fingerprint가 달라졌으므로
        # 무효 처리되어야 한다.
        self.assertIsNone(
            self.approval_service.current_valid_approval(
                listing.id, selection.id, self.company_id,
            ),
        )

    def test_stale_approval_request_after_change_fails_fresh_check(self):
        """
        request_approval() 이후, approve() 시점 사이에 정책 버전이나
        listing/selection 상태가 바뀌면 _fresh_check()가 ConflictException
        으로 승인 자체를 막는다.
        """

        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        capability = self._capability(channel.id)
        listing, selection = self._listing_with_selection(
            candidate.id, account.id,
        )
        pending = self._request_approval(listing, selection)

        # 요청 이후 정책 버전이 바뀐 상황을 시뮬레이션.
        capability.policy_version = "2.0.0"
        self.db.commit()

        with self.assertRaises(ConflictException):
            self.approval_service.approve(
                pending.id,
                MarketplaceSubmissionApprovalDecisionRequest(
                    expires_at=datetime.utcnow() + timedelta(days=1),
                    idempotency_key=_next_key("appdec"),
                ),
                approved_by=42, company_id=self.company_id,
            )

    # ----------------------------------------------------
    # 3) 승인과 제출은 분리된 별도 요청이다
    # ----------------------------------------------------

    def test_submit_has_no_approval_related_parameter(self):
        """
        SubmissionService.submit()의 시그니처에 승인을 나타내는
        파라미터가 없다는 것(request와 회사 소유권 스코프인
        company_id만 받음)을 확인한다 — 승인 여부 자체는 오직 별도
        ApprovalService 호출을 통해서만 이루어진다(2026-08-01 CTO
        3차 지적 반영으로 company_id가 추가됐다 — 이것은 회사 소유권
        스코프이지 승인을 자칭하는 파라미터가 아니다).
        """

        import inspect

        signature = inspect.signature(SubmissionService.submit)
        params = list(signature.parameters.keys())

        self.assertEqual(params, ["self", "data", "company_id"])

    def test_request_approval_and_approve_are_separate_calls(self):
        """
        request_approval()과 approve()가 서로 다른 메서드 호출이며,
        request_approval()만으로는 절대 유효한 승인이 되지 않는다.
        """

        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)
        listing, selection = self._listing_with_selection(
            candidate.id, account.id,
        )

        pending = self._request_approval(listing, selection)
        self.assertIsNone(
            self.approval_service.current_valid_approval(
                listing.id, selection.id, self.company_id,
            ),
        )

        self.approval_service.approve(
            pending.id,
            MarketplaceSubmissionApprovalDecisionRequest(
                expires_at=datetime.utcnow() + timedelta(days=1),
                idempotency_key=_next_key("appdec"),
            ),
            approved_by=42, company_id=self.company_id,
        )
        self.assertIsNotNone(
            self.approval_service.current_valid_approval(
                listing.id, selection.id, self.company_id,
            ),
        )


if __name__ == "__main__":
    unittest.main()
