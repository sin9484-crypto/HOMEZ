"""
=========================================================
Homez OS

File : tests/test_marketplace_submission.py

채널별 제출 검증(2026-07-31 재감사 반영 — 전면 재작성):
15) 승인 없는 제출 차단(서버 저장 승인 레코드 기준)
16) Emergency Stop 최우선
17) 동일 채널 중복 제출 차단
18) 복수 채널 부분 성공 상태
19) timeout(어댑터 예외) UNKNOWN 처리
20) 채널별 감사 로그(제출 이력)
+ 신규: 금액·수량 0 하드코딩 방지, Safety 입력 부족 차단, 실제 자금
  노출 한도 검사(자유 JSON이 아니라 검증된 Schema 기반 payload 사용)
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import ForbiddenException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.constants import SafetyDecision
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.automation_safety.service import SafetyService
from app.domains.channel_policy.model import ChannelPolicyEvaluation
from app.domains.channel_policy.model import ChannelPolicyRule
from app.domains.channel_policy.model import CompanyChannelPolicySettings
from app.domains.marketplace_listing.approval_service import ApprovalService
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
from app.domains.media_asset.model import MediaAsset
from tests.channel_policy_test_helpers import seed_channel_eligible_policy
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


class MarketplaceSubmissionTestCase(unittest.TestCase):

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
            name="테스트 회사", business_number="333-33-33333", ceo="테스트",
            phone="02-000-0000", email="sub@example.com", address="서울",
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
        """
        기본 AutomationMode는 RECOMMEND_ONLY(항상 DENY)이므로, 제출
        경로 자체를 검증하려면 명시적으로 OPERATOR_APPROVAL로 바꾼다.
        """

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

    def _listing_with_selection(
        self, candidate_id, account_id, mode=FulfillmentMode.SELLER_FULFILLED,
        required_fields=None,
    ):

        listing, _dup = self.listing_service.create_listing(
            MarketplaceListingCreateRequest(
                product_candidate_id=candidate_id,
                marketplace_account_id=account_id,
            ),
            self.company_id,
        )
        selection, _dup, _events = self.listing_service.select_fulfillment_mode(
            MarketplaceFulfillmentSelectionCreateRequest(
                listing_id=listing.id, fulfillment_mode=mode,
                required_fields=(
                    required_fields
                    if required_fields is not None
                    else VALID_COUPANG_SELLER_FULFILLED_FIELDS
                ),
                idempotency_key=_next_key("sel"),
            ),
            selected_by=1, company_id=self.company_id,
        )

        return listing, selection

    def _approve(
        self, listing, selection, planned_quantity=10,
        unit_price=Decimal("9000"), unit_cost_of_goods=Decimal("5000"),
        expected_logistics_cost=Decimal("500"),
        expires_at=None,
    ) -> MarketplaceSubmissionApproval:

        pending, _dup = self.approval_service.request_approval(
            MarketplaceSubmissionApprovalRequestRequest(
                listing_id=listing.id, selection_id=selection.id,
                planned_quantity=planned_quantity, unit_price=unit_price,
                unit_cost_of_goods=unit_cost_of_goods,
                expected_logistics_cost=expected_logistics_cost,
                idempotency_key=_next_key("appreq"),
            ),
            requested_by=1, company_id=self.company_id,
        )
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

        # CA-1(2026-08-21) — 제출 직전 채널 정책 게이트가 유효한
        # 평가를 요구한다. 승인과 마찬가지로 이 시점의 listing/
        # selection 상태를 그대로 지문에 새긴다.
        account = self.db.get(MarketplaceAccount, listing.marketplace_account_id)
        channel = self.db.get(MarketplaceChannel, account.channel_id)
        seed_channel_eligible_policy(
            self.db, company_id=self.company_id,
            product_candidate_id=listing.product_candidate_id,
            channel=channel.code, selection_id=selection.id,
        )

        return approved

    # ----------------------------------------------------
    # 15) 승인 없는 제출 차단 (서버 저장 승인 레코드 기준)
    # ----------------------------------------------------

    def test_submission_without_any_approval_is_blocked(self):

        self._allow_automation()
        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)
        listing, selection = self._listing_with_selection(
            candidate.id, account.id,
        )
        # 이 테스트가 실제로 검증하려는 것은 승인 게이트다 — 채널
        # 정책 게이트는 통과시켜 승인 게이트만 단독으로 관측한다.
        seed_channel_eligible_policy(
            self.db, company_id=self.company_id,
            product_candidate_id=candidate.id,
            channel=channel.code, selection_id=selection.id,
        )

        with self.assertRaises(ForbiddenException):
            self.submission_service.submit(
                MarketplaceSubmissionRequest(
                    listing_id=listing.id, selection_id=selection.id,
                    idempotency_key=_next_key("sub"),
                ),
                self.company_id,
            )

    def test_submission_with_valid_approval_succeeds_structurally(self):

        self._allow_automation()
        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)
        listing, selection = self._listing_with_selection(
            candidate.id, account.id,
        )
        approval = self._approve(listing, selection)

        result = self.submission_service.submit(
            MarketplaceSubmissionRequest(
                listing_id=listing.id, selection_id=selection.id,
                idempotency_key=_next_key("sub"),
            ),
            self.company_id,
        )

        self.assertEqual(result.status, "PENDING")
        self.assertEqual(result.safety_decision, SafetyDecision.ALLOW)
        self.assertEqual(result.operator_approved_by, approval.approved_by)

    # ----------------------------------------------------
    # 16) Emergency Stop 최우선
    # ----------------------------------------------------

    def test_emergency_stop_blocks_submission_even_with_valid_approval(self):

        self._allow_automation()
        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)
        listing, selection = self._listing_with_selection(
            candidate.id, account.id,
        )
        self._approve(listing, selection)

        self.safety_service.activate_emergency_stop(
            reason="테스트 비상 정지", set_by=1, is_admin=True,
        )

        result = self.submission_service.submit(
            MarketplaceSubmissionRequest(
                listing_id=listing.id, selection_id=selection.id,
                idempotency_key=_next_key("sub"),
            ),
            self.company_id,
        )

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.safety_decision, SafetyDecision.DENY)
        self.assertIsNone(result.operator_approved_by)

    # ----------------------------------------------------
    # 17) 동일 채널 중복 제출 차단
    # ----------------------------------------------------

    def test_duplicate_submission_idempotency_key_returns_same_row(self):

        self._allow_automation()
        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)
        listing, selection = self._listing_with_selection(
            candidate.id, account.id,
        )
        self._approve(listing, selection)

        key = _next_key("sub")
        first = self.submission_service.submit(
            MarketplaceSubmissionRequest(
                listing_id=listing.id, selection_id=selection.id,
                idempotency_key=key,
            ),
            self.company_id,
        )
        second = self.submission_service.submit(
            MarketplaceSubmissionRequest(
                listing_id=listing.id, selection_id=selection.id,
                idempotency_key=key,
            ),
            self.company_id,
        )

        self.assertEqual(first.id, second.id)

    # ----------------------------------------------------
    # 18) 복수 채널 부분 성공 상태
    # ----------------------------------------------------

    def test_partial_success_across_channels_is_independent(self):

        self._allow_automation()
        candidate = self._candidate()

        coupang = self._channel("COUPANG", "쿠팡")
        naver = self._channel("NAVER_SMARTSTORE", "네이버 스마트스토어")

        coupang_account = self._account(coupang.id, "c1")
        naver_account = self._account(naver.id, "n1")

        self._capability(coupang.id, FulfillmentMode.SELLER_FULFILLED)
        # 네이버는 SELLER_FULFILLED만 지원하는데, 여기선 일부러
        # MARKETPLACE_FULFILLED를 VERIFIED로 등록해 어댑터가
        # NOT_SUPPORTED를 반환하는 상황을 재현한다. required_fields
        # Schema도 등록되어 있지 않으므로 select_fulfillment_mode
        # 단계에서 이미 막히는 게 정상 — 대신 이 케이스는 select 단계를
        # 성공시키기 위해 실제 등록된 SELLER_FULFILLED로 선택하고,
        # 제출 단계에서 어댑터가 이를 처리하지 못하는 상황(다른 채널
        # 코드 취급)을 만들지 않고, 더 직접적으로 캡빌리티 자체가
        # is_supported=True인 미등록 조합을 시뮬레이션한다.
        naver_capability = MarketplaceFulfillmentCapability(
            channel_id=naver.id,
            fulfillment_mode=FulfillmentMode.MARKETPLACE_FULFILLED,
            is_supported=True, requires_eligibility_check=False,
            requires_account_contract=False,
            external_display_name="테스트 미지원 모드",
            policy_version="1.0.0",
            # 이 조합은 required_fields_schemas 레지스트리에 없다 —
            # select_fulfillment_mode 자체가 이를 차단하는지 검증한다.
            schema_name=None, schema_version=None,
            doc_source_reference="test", status=CapabilityStatus.VERIFIED,
            verified_at=datetime.utcnow(),
        )
        self.db.add(naver_capability)
        self.db.commit()

        coupang_listing, coupang_selection = self._listing_with_selection(
            candidate.id, coupang_account.id, FulfillmentMode.SELLER_FULFILLED,
        )

        # 네이버 쪽은 Schema가 등록되지 않은 조합이므로 selection
        # 단계에서부터 차단되어야 한다(요청 원문: 미지원 조합은 서버가
        # 차단).
        naver_listing, _dup = self.listing_service.create_listing(
            MarketplaceListingCreateRequest(
                product_candidate_id=candidate.id,
                marketplace_account_id=naver_account.id,
            ),
            self.company_id,
        )
        from app.core.exceptions import BadRequestException
        with self.assertRaises(BadRequestException):
            self.listing_service.select_fulfillment_mode(
                MarketplaceFulfillmentSelectionCreateRequest(
                    listing_id=naver_listing.id,
                    fulfillment_mode=FulfillmentMode.MARKETPLACE_FULFILLED,
                    required_fields={"x": "y"},
                    idempotency_key=_next_key("sel"),
                ),
                selected_by=1, company_id=self.company_id,
            )

        # 쿠팡 쪽은 정상적으로 승인·제출까지 독립적으로 성공한다 —
        # 네이버 쪽이 selection 단계에서 막힌 것과 무관하다.
        self._approve(coupang_listing, coupang_selection)
        coupang_result = self.submission_service.submit(
            MarketplaceSubmissionRequest(
                listing_id=coupang_listing.id,
                selection_id=coupang_selection.id,
                idempotency_key=_next_key("sub"),
            ),
            self.company_id,
        )
        self.assertEqual(coupang_result.status, "PENDING")

    # ----------------------------------------------------
    # 19) timeout(어댑터 예외) UNKNOWN 처리
    # ----------------------------------------------------

    def test_adapter_exception_is_recorded_as_unknown_not_inferred(self):

        self._allow_automation()
        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)
        listing, selection = self._listing_with_selection(
            candidate.id, account.id,
        )
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

    # ----------------------------------------------------
    # 20) 채널별 감사 로그(제출 이력)
    # ----------------------------------------------------

    def test_submissions_are_append_only_per_listing_audit_trail(self):

        self._allow_automation()
        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)
        listing, selection = self._listing_with_selection(
            candidate.id, account.id,
        )
        self._approve(listing, selection)

        self.submission_service.submit(
            MarketplaceSubmissionRequest(
                listing_id=listing.id, selection_id=selection.id,
                idempotency_key=_next_key("sub"),
            ),
            self.company_id,
        )
        self.submission_service.submit(
            MarketplaceSubmissionRequest(
                listing_id=listing.id, selection_id=selection.id,
                idempotency_key=_next_key("sub"),
            ),
            self.company_id,
        )

        history = self.submission_service.list_submissions(listing.id, self.company_id)

        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].marketplace_account_id, account.id)

    # ----------------------------------------------------
    # 신규: 금액·수량 0 하드코딩 방지
    # ----------------------------------------------------

    def test_safety_service_receives_real_computed_funding_not_zero(self):

        self._allow_automation()
        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)
        listing, selection = self._listing_with_selection(
            candidate.id, account.id,
        )
        self._approve(
            listing, selection, planned_quantity=7,
            unit_cost_of_goods=Decimal("3000"),
            expected_logistics_cost=Decimal("400"),
        )

        with mock.patch.object(
            self.safety_service, "evaluate",
            wraps=self.safety_service.evaluate,
        ) as spy:
            self.submission_service.submit(
                MarketplaceSubmissionRequest(
                    listing_id=listing.id, selection_id=selection.id,
                    idempotency_key=_next_key("sub"),
                ),
                self.company_id,
            )

        _args, kwargs = spy.call_args
        # 3000*7 + 400 = 21400 — 절대 0이 아니다.
        self.assertEqual(kwargs["funding_amount"], 21400.0)
        self.assertEqual(kwargs["quantity"], 7)
        self.assertNotEqual(kwargs["funding_amount"], 0)
        self.assertNotEqual(kwargs["quantity"], 0)

    # ----------------------------------------------------
    # 신규: Safety 입력 부족 차단
    # ----------------------------------------------------

    def test_insufficient_safety_input_blocks_when_quantity_invalid(self):
        """
        planned_quantity는 스키마 레벨(Field(gt=0))에서 이미 양수만
        허용하지만, 승인 스냅샷 자체가 조작/손상되어 0 이하가 저장된
        극단적 상황을 서비스 레벨에서도 방어하는지 직접 검증한다.
        """

        self._allow_automation()
        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)
        listing, selection = self._listing_with_selection(
            candidate.id, account.id,
        )
        approval = self._approve(listing, selection, planned_quantity=1)

        # 승인 행을 직접 조작해 quantity<=0인 상태를 재현한다(스키마를
        # 우회한 저장소 손상 시나리오 방어 확인).
        approval.planned_quantity = 0
        self.db.commit()

        result = self.submission_service.submit(
            MarketplaceSubmissionRequest(
                listing_id=listing.id, selection_id=selection.id,
                idempotency_key=_next_key("sub"),
            ),
            self.company_id,
        )

        self.assertEqual(result.status, "FAILED")
        self.assertIn("INSUFFICIENT_SAFETY_INPUT", result.error_reason)

    # ----------------------------------------------------
    # 신규: 실제 자금 노출 한도 검사
    # ----------------------------------------------------

    def test_real_funding_exposure_limit_enforcement(self):

        self._allow_automation()

        from app.domains.automation_safety.model import ExecutionLimit

        # 아주 낮은 한도를 설정해, 계산된 실제 funding_amount가 이를
        # 초과하도록 만든다.
        low_limit = ExecutionLimit(
            product_id=None, daily_funding_limit=100.0,
            per_product_funding_limit=None, daily_quantity_limit=None,
            per_product_quantity_limit=None, currency="KRW",
            period="DAILY", active=True,
        )
        self.db.add(low_limit)
        self.db.commit()

        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)
        listing, selection = self._listing_with_selection(
            candidate.id, account.id,
        )
        # unit_cost_of_goods * quantity + logistics = 5000*10+500 =
        # 50500, 훨씬 위인 100 한도를 초과한다.
        self._approve(listing, selection)

        result = self.submission_service.submit(
            MarketplaceSubmissionRequest(
                listing_id=listing.id, selection_id=selection.id,
                idempotency_key=_next_key("sub"),
            ),
            self.company_id,
        )

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.safety_decision, SafetyDecision.DENY)


if __name__ == "__main__":
    unittest.main()
