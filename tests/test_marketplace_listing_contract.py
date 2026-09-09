"""
=========================================================
Homez OS

File : tests/test_marketplace_listing_contract.py

채널별 판매 방식 선택 — 계약 검증:
1) 선택한 채널마다 판매 방식 필수
2) 같은 상품의 채널별 서로 다른 방식 저장
3) 같은 채널의 계정별 서로 다른 방식 저장
11) 방식별 필수 입력 누락 거부

재감사 신규 (2026-07-31):
13) ProductCandidate → Listing 계약 — APPROVED 상태만 허용
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.database.base import Base
from app.domains.company.model import Company
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
    MarketplaceListingDraftCreateRequest,
)
from app.domains.marketplace_listing.service import MarketplaceListingService
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

VALID_COUPANG_ROCKET_GROWTH_FIELDS = {
    "barcode": "8801234567890",
    "priceData": {"salePrice": "9000", "originalPrice": "10000"},
    "skuInfo": {
        "weight": "1.5", "width": "10", "length": "10", "height": "10",
        "fragile": False, "quantityPerBox": 1,
    },
    "distributionPeriod": 365,
    "expiredAtManaged": False,
    "rocketGrowthAdditionalInformation": {
        "rfmInboundName": "테스트", "legalAgreement": "AGREE",
    },
}

VALID_NAVER_SELLER_FULFILLED_FIELDS = {
    "deliveryType": "DELIVERY", "deliveryAttributeType": "NORMAL",
}

REQUIRED_FIELDS_BY_MODE_AND_CHANNEL = {
    ("COUPANG", FulfillmentMode.SELLER_FULFILLED): (
        VALID_COUPANG_SELLER_FULFILLED_FIELDS
    ),
    ("COUPANG", FulfillmentMode.MARKETPLACE_FULFILLED): (
        VALID_COUPANG_ROCKET_GROWTH_FIELDS
    ),
    ("NAVER_SMARTSTORE", FulfillmentMode.SELLER_FULFILLED): (
        VALID_NAVER_SELLER_FULFILLED_FIELDS
    ),
}

SCHEMA_INFO_BY_MODE_AND_CHANNEL = {
    ("COUPANG", FulfillmentMode.SELLER_FULFILLED): (
        "coupang_seller_fulfilled", "1.0.0",
    ),
    ("COUPANG", FulfillmentMode.MARKETPLACE_FULFILLED): (
        "coupang_rocket_growth", "1.0.0",
    ),
    ("NAVER_SMARTSTORE", FulfillmentMode.SELLER_FULFILLED): (
        "naver_seller_fulfilled", "1.0.0",
    ),
}


class MarketplaceListingContractTestCase(unittest.TestCase):

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
            name="테스트 회사", business_number="222-22-22222", ceo="테스트",
            phone="02-000-0000", email="contract@example.com", address="서울",
        )
        self.db.add(company)
        self.db.commit()
        self.company_id = company.id

        self.service = MarketplaceListingService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _candidate(
        self, ref="CAND-1", status=CandidateStatus.APPROVED,
    ) -> ProductCandidate:

        candidate = ProductCandidate(
            candidate_key=f"test:COUPANG:{ref}",
            source_type="TREND",
            source_reference=ref,
            market="COUPANG",
            product_name="테스트 상품",
            status=status,
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

    def _account(
        self, channel_id: int, code="acct-1", name="계정1",
    ) -> MarketplaceAccount:

        account = MarketplaceAccount(
            company_id=self.company_id, channel_id=channel_id,
            account_code=code, account_name=name,
        )
        self.db.add(account)
        self.db.commit()

        return account

    def _verified_capability(
        self, channel_code: str, channel_id: int, mode: str,
        requires_eligibility=False,
    ) -> MarketplaceFulfillmentCapability:

        schema_name, schema_version = SCHEMA_INFO_BY_MODE_AND_CHANNEL[
            (channel_code, mode)
        ]

        capability = MarketplaceFulfillmentCapability(
            channel_id=channel_id,
            fulfillment_mode=mode,
            is_supported=True,
            requires_eligibility_check=requires_eligibility,
            requires_account_contract=False,
            external_display_name=mode,
            policy_version="1.0.0",
            schema_name=schema_name,
            schema_version=schema_version,
            doc_source_reference="test",
            status=CapabilityStatus.VERIFIED,
            verified_at=datetime.utcnow(),
        )
        self.db.add(capability)
        self.db.commit()

        return capability

    def _listing(
        self, candidate_id: int, account_id: int,
    ) -> MarketplaceListing:

        listing, _dup = self.service.create_listing(
            MarketplaceListingCreateRequest(
                product_candidate_id=candidate_id,
                marketplace_account_id=account_id,
            ),
            self.company_id,
        )

        return listing

    def _select_mode(
        self, listing_id: int, channel_code: str, mode: str, selected_by=1,
    ) -> MarketplaceFulfillmentSelection:

        selection, _dup, _events = self.service.select_fulfillment_mode(
            MarketplaceFulfillmentSelectionCreateRequest(
                listing_id=listing_id,
                fulfillment_mode=mode,
                required_fields=REQUIRED_FIELDS_BY_MODE_AND_CHANNEL[
                    (channel_code, mode)
                ],
                idempotency_key=_next_key("sel"),
            ),
            selected_by=selected_by, company_id=self.company_id,
        )

        return selection

    # ----------------------------------------------------
    # 1) 선택한 채널마다 판매 방식 필수
    # ----------------------------------------------------

    def test_channel_required_mode_blocks_finalize_when_missing(self):

        candidate = self._candidate()
        channel = self._channel()
        account_a = self._account(channel.id, "a", "계정A")
        account_b = self._account(channel.id, "b", "계정B")
        self._verified_capability(
            "COUPANG", channel.id, FulfillmentMode.SELLER_FULFILLED,
        )

        draft, _dup = self.service.create_draft(
            MarketplaceListingDraftCreateRequest(
                product_candidate_id=candidate.id,
                idempotency_key=_next_key("draft"),
            ),
            created_by=1, company_id=self.company_id,
        )
        draft = self.service.select_channels(
            draft.id, [account_a.id, account_b.id], self.company_id,
        )

        listing_a = self._listing(candidate.id, account_a.id)
        listing_b = self._listing(candidate.id, account_b.id)

        # account_a만 방식을 선택하고 account_b는 선택하지 않는다.
        self._select_mode(
            listing_a.id, "COUPANG", FulfillmentMode.SELLER_FULFILLED,
        )

        with self.assertRaises(BadRequestException):
            self.service.finalize_fulfillment_selection(
                draft.id, self.company_id,
            )

        # account_b도 선택하면 완료 확인이 통과한다.
        self._select_mode(
            listing_b.id, "COUPANG", FulfillmentMode.SELLER_FULFILLED,
        )
        finalized = self.service.finalize_fulfillment_selection(
            draft.id, self.company_id,
        )
        self.assertEqual(finalized.workflow_state, "FULFILLMENT_SELECTED")

    def test_no_channel_selected_means_no_fulfillment_required(self):
        """채널 미선택이면 판매 방식 불필요 — Draft가 DRAFT 상태 그대로."""

        candidate = self._candidate()

        draft, _dup = self.service.create_draft(
            MarketplaceListingDraftCreateRequest(
                product_candidate_id=candidate.id,
                idempotency_key=_next_key("draft"),
            ),
            created_by=1, company_id=self.company_id,
        )

        self.assertEqual(draft.workflow_state, "DRAFT")
        self.assertEqual(draft.selected_channel_ids_json, "[]")

    # ----------------------------------------------------
    # 2) 같은 상품의 채널별 서로 다른 방식 저장
    # ----------------------------------------------------

    def test_same_candidate_different_channels_store_independent_modes(self):

        candidate = self._candidate()

        coupang = self._channel("COUPANG", "쿠팡")
        naver = self._channel("NAVER_SMARTSTORE", "네이버 스마트스토어")

        coupang_account = self._account(coupang.id, "c1", "쿠팡 계정")
        naver_account = self._account(naver.id, "n1", "네이버 계정")

        self._verified_capability(
            "COUPANG", coupang.id, FulfillmentMode.MARKETPLACE_FULFILLED,
        )
        self._verified_capability(
            "NAVER_SMARTSTORE", naver.id, FulfillmentMode.SELLER_FULFILLED,
        )

        coupang_listing = self._listing(candidate.id, coupang_account.id)
        naver_listing = self._listing(candidate.id, naver_account.id)

        self._select_mode(
            coupang_listing.id, "COUPANG",
            FulfillmentMode.MARKETPLACE_FULFILLED,
        )
        self._select_mode(
            naver_listing.id, "NAVER_SMARTSTORE",
            FulfillmentMode.SELLER_FULFILLED,
        )

        coupang_selection = self.service.get_current_selection(
            coupang_listing.id, self.company_id,
        )
        naver_selection = self.service.get_current_selection(
            naver_listing.id, self.company_id,
        )

        self.assertEqual(
            coupang_selection.fulfillment_mode,
            FulfillmentMode.MARKETPLACE_FULFILLED,
        )
        self.assertEqual(
            naver_selection.fulfillment_mode, FulfillmentMode.SELLER_FULFILLED,
        )

    # ----------------------------------------------------
    # 3) 같은 채널의 계정별 서로 다른 방식 저장
    # ----------------------------------------------------

    def test_same_channel_different_accounts_store_independent_modes(self):

        candidate = self._candidate()
        channel = self._channel()

        account_a = self._account(channel.id, "a", "계정A")
        account_b = self._account(channel.id, "b", "계정B")

        self._verified_capability(
            "COUPANG", channel.id, FulfillmentMode.SELLER_FULFILLED,
        )
        self._verified_capability(
            "COUPANG", channel.id, FulfillmentMode.MARKETPLACE_FULFILLED,
        )

        listing_a = self._listing(candidate.id, account_a.id)
        listing_b = self._listing(candidate.id, account_b.id)

        self._select_mode(
            listing_a.id, "COUPANG", FulfillmentMode.SELLER_FULFILLED,
        )
        self._select_mode(
            listing_b.id, "COUPANG", FulfillmentMode.MARKETPLACE_FULFILLED,
        )

        selection_a = self.service.get_current_selection(
            listing_a.id, self.company_id,
        )
        selection_b = self.service.get_current_selection(
            listing_b.id, self.company_id,
        )

        self.assertEqual(
            selection_a.fulfillment_mode, FulfillmentMode.SELLER_FULFILLED,
        )
        self.assertEqual(
            selection_b.fulfillment_mode,
            FulfillmentMode.MARKETPLACE_FULFILLED,
        )

        # 같은 product_candidate_id + marketplace_id 여도 계정이 다르면
        # UNIQUE 위반이 나지 않는다(둘 다 정상 생성됨을 위에서 이미 확인).
        self.assertNotEqual(listing_a.id, listing_b.id)

    # ----------------------------------------------------
    # 11) 방식별 필수 입력 누락 거부
    # ----------------------------------------------------

    def test_missing_required_fields_is_rejected(self):

        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._verified_capability(
            "COUPANG", channel.id, FulfillmentMode.SELLER_FULFILLED,
        )
        listing = self._listing(candidate.id, account.id)

        with self.assertRaises(BadRequestException):
            self.service.select_fulfillment_mode(
                MarketplaceFulfillmentSelectionCreateRequest(
                    listing_id=listing.id,
                    fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                    required_fields={},
                    idempotency_key=_next_key("sel"),
                ),
                selected_by=1, company_id=self.company_id,
            )

    # ----------------------------------------------------
    # 13) ProductCandidate → Listing 계약
    # ----------------------------------------------------

    def test_listing_creation_requires_approved_candidate(self):

        channel = self._channel()
        account = self._account(channel.id)

        for status in (
            CandidateStatus.DISCOVERED, CandidateStatus.ANALYZED,
            CandidateStatus.RECOMMENDED, CandidateStatus.HELD,
            CandidateStatus.REJECTED, CandidateStatus.EXPIRED,
        ):
            candidate = self._candidate(ref=f"CAND-{status}", status=status)

            with self.assertRaises(BadRequestException):
                self.service.create_listing(
                    MarketplaceListingCreateRequest(
                        product_candidate_id=candidate.id,
                        marketplace_account_id=account.id,
                    ),
                    self.company_id,
                )

    def test_listing_creation_succeeds_for_approved_candidate(self):

        candidate = self._candidate(status=CandidateStatus.APPROVED)
        channel = self._channel()
        account = self._account(channel.id)

        listing, _dup = self.service.create_listing(
            MarketplaceListingCreateRequest(
                product_candidate_id=candidate.id,
                marketplace_account_id=account.id,
            ),
            self.company_id,
        )

        self.assertEqual(listing.product_candidate_id, candidate.id)

    def test_product_candidate_never_treated_as_permanent_sellable_product(
        self,
    ):
        """
        ProductCandidate 자체에는 판매 가능 여부를 나타내는 필드가
        없고, 오직 MarketplaceListing 생성이라는 명시적 전이를 거쳐야만
        판매 채널에 연결된다 — candidate.status가 이후 바뀌어도(예:
        운영자가 실수로 되돌림) 이미 생성된 Listing 자체는 남아있지만,
        새로운 Listing 생성 시도는 그 시점의 실제 상태를 기준으로 다시
        확인된다.
        """

        candidate = self._candidate(status=CandidateStatus.APPROVED)
        channel = self._channel()
        account = self._account(channel.id)

        listing, _dup = self.service.create_listing(
            MarketplaceListingCreateRequest(
                product_candidate_id=candidate.id,
                marketplace_account_id=account.id,
            ),
            self.company_id,
        )
        self.assertIsNotNone(listing.id)

        # 운영자가 사후에 후보 상태를 되돌린 상황을 재현한다.
        candidate.status = CandidateStatus.HELD
        self.db.commit()

        second_account = self._account(channel.id, "b", "계정B")
        with self.assertRaises(BadRequestException):
            self.service.create_listing(
                MarketplaceListingCreateRequest(
                    product_candidate_id=candidate.id,
                    marketplace_account_id=second_account.id,
                ),
                self.company_id,
            )


if __name__ == "__main__":
    unittest.main()
