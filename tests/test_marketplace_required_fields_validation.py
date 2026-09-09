"""
=========================================================
Homez OS

File : tests/test_marketplace_required_fields_validation.py

필수 입력 Schema 재감사 신규 테스트 (2026-07-31):
4) 필수 입력 타입/범위/미지원 필드 검증
5) 저장된 JSON 손상 시 fail-closed
=========================================================
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
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
from app.domains.marketplace_listing.required_fields_schemas import (
    CoupangRocketGrowthFields,
    CoupangSellerFulfilledFields,
    NaverSellerFulfilledFields,
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
    "deliveryMethod": "SEQUENCIAL",
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


class RequiredFieldsSchemaUnitTestCase(unittest.TestCase):
    """
    DB 없이 순수 Pydantic Schema 레벨에서의 타입/범위/미지원 필드
    검증 (시나리오 4).
    """

    def test_unknown_field_is_rejected(self):

        payload = dict(VALID_COUPANG_SELLER_FULFILLED_FIELDS)
        payload["unexpectedField"] = "이건 없어야 하는 필드"

        with self.assertRaises(ValidationError):
            CoupangSellerFulfilledFields.model_validate(payload)

    def test_negative_price_is_rejected(self):

        payload = dict(VALID_COUPANG_SELLER_FULFILLED_FIELDS)
        payload["salePrice"] = "-1"

        with self.assertRaises(ValidationError):
            CoupangSellerFulfilledFields.model_validate(payload)

    def test_zero_maximum_buy_count_is_rejected(self):

        payload = dict(VALID_COUPANG_SELLER_FULFILLED_FIELDS)
        payload["maximumBuyCount"] = 0

        with self.assertRaises(ValidationError):
            CoupangSellerFulfilledFields.model_validate(payload)

    def test_wrong_type_for_items_is_rejected(self):

        payload = dict(VALID_COUPANG_SELLER_FULFILLED_FIELDS)
        payload["items"] = "이건 리스트여야 한다"

        with self.assertRaises(ValidationError):
            CoupangSellerFulfilledFields.model_validate(payload)

    def test_empty_items_list_is_rejected(self):

        payload = dict(VALID_COUPANG_SELLER_FULFILLED_FIELDS)
        payload["items"] = []

        with self.assertRaises(ValidationError):
            CoupangSellerFulfilledFields.model_validate(payload)

    def test_duplicate_item_sku_is_rejected(self):
        # 2026-08-29 쿠팡 상품등록 핵심 차단 해결(Section 6 집중
        # 테스트 5번 "중복 SKU 차단").
        payload = dict(VALID_COUPANG_SELLER_FULFILLED_FIELDS)
        payload["items"] = [
            {"itemName": "레드", "externalVendorSku": "SKU-DUP"},
            {"itemName": "블루", "externalVendorSku": "SKU-DUP"},
        ]

        with self.assertRaises(ValidationError):
            CoupangSellerFulfilledFields.model_validate(payload)

    def test_duplicate_item_name_is_rejected(self):
        # 2026-08-31 Phase 7.6 — 공식 문서(Product Creation) 원문
        # "Input for each item so that there is no overlap"로 확정된
        # 계약. SKU가 달라도 itemName이 같으면 옵션을 구분할 수 없다.
        payload = dict(VALID_COUPANG_SELLER_FULFILLED_FIELDS)
        payload["items"] = [
            {"itemName": "같은이름", "externalVendorSku": "SKU-A"},
            {"itemName": "같은이름", "externalVendorSku": "SKU-B"},
        ]

        with self.assertRaises(ValidationError):
            CoupangSellerFulfilledFields.model_validate(payload)

    def test_empty_item_name_is_rejected(self):
        # 공식 문서상 itemName은 필수(*)다 — 빈 문자열은 "입력하지
        # 않은 것"과 같게 취급해 막는다.
        payload = dict(VALID_COUPANG_SELLER_FULFILLED_FIELDS)
        payload["items"] = [{"itemName": "", "externalVendorSku": "SKU-001"}]

        with self.assertRaises(ValidationError):
            CoupangSellerFulfilledFields.model_validate(payload)

    def test_item_name_over_150_chars_is_rejected(self):
        # 공식 문서 확인: itemName 최대 150자.
        payload = dict(VALID_COUPANG_SELLER_FULFILLED_FIELDS)
        payload["items"] = [
            {"itemName": "가" * 151, "externalVendorSku": "SKU-001"},
        ]

        with self.assertRaises(ValidationError):
            CoupangSellerFulfilledFields.model_validate(payload)

    def test_item_name_at_exactly_150_chars_is_accepted(self):

        payload = dict(VALID_COUPANG_SELLER_FULFILLED_FIELDS)
        payload["items"] = [
            {"itemName": "가" * 150, "externalVendorSku": "SKU-001"},
        ]

        validated = CoupangSellerFulfilledFields.model_validate(payload)

        self.assertEqual(len(validated.items[0].itemName), 150)

    def test_per_item_price_and_stock_override_is_accepted(self):
        # 2026-08-29 — Section 6 집중 테스트 4번("옵션 조합별 가격·
        # 재고·SKU"). 공식 문서 확인 결과 이 필드들은 items[] 레벨
        # 필드다 — 선택적 오버라이드로 허용해야 한다.
        payload = dict(VALID_COUPANG_SELLER_FULFILLED_FIELDS)
        payload["items"] = [
            {
                "itemName": "레드", "externalVendorSku": "SKU-RED",
                "originalPrice": "12000", "salePrice": "11000",
                "maximumBuyCount": 5, "unitCount": 40,
            },
            {"itemName": "블루", "externalVendorSku": "SKU-BLUE"},
        ]

        validated = CoupangSellerFulfilledFields.model_validate(payload)

        self.assertEqual(validated.items[0].salePrice, Decimal("11000"))
        self.assertEqual(validated.items[0].maximumBuyCount, 5)
        self.assertIsNone(validated.items[1].salePrice)

    def test_invalid_delivery_charge_type_literal_is_rejected(self):

        payload = dict(VALID_COUPANG_SELLER_FULFILLED_FIELDS)
        payload["deliveryChargeType"] = "SOMETHING_NOT_IN_DOCS"

        with self.assertRaises(ValidationError):
            CoupangSellerFulfilledFields.model_validate(payload)

    def test_invalid_delivery_method_literal_is_rejected(self):

        payload = dict(VALID_COUPANG_SELLER_FULFILLED_FIELDS)
        payload["deliveryMethod"] = "NORMAL"

        with self.assertRaises(ValidationError):
            CoupangSellerFulfilledFields.model_validate(payload)

    def test_all_documented_delivery_methods_are_accepted(self):

        for delivery_method in (
            "SEQUENCIAL", "COLD_FRESH", "MAKE_ORDER", "AGENT_BUY",
            "VENDOR_DIRECT",
        ):
            with self.subTest(delivery_method=delivery_method):
                payload = dict(VALID_COUPANG_SELLER_FULFILLED_FIELDS)
                payload["deliveryMethod"] = delivery_method
                validated = CoupangSellerFulfilledFields.model_validate(payload)
                self.assertEqual(validated.deliveryMethod, delivery_method)

    def test_money_fields_are_decimal_not_float(self):

        validated = CoupangSellerFulfilledFields.model_validate(
            VALID_COUPANG_SELLER_FULFILLED_FIELDS,
        )

        self.assertIsInstance(validated.salePrice, Decimal)
        self.assertIsInstance(validated.originalPrice, Decimal)

    def test_category_and_notice_payload_from_listing_wizard_is_accepted(self):

        payload = dict(VALID_COUPANG_SELLER_FULFILLED_FIELDS)
        payload.update({
            "displayCategoryCode": 80754,
            "notices": [{
                "noticeCategoryName": "주방용품",
                "noticeCategoryDetailNames": [{
                    "noticeCategoryDetailName": "품명 및 모델명",
                    "content": "국산 삼색 부직포 주방행주 40매",
                }],
            }],
        })

        validated = CoupangSellerFulfilledFields.model_validate(payload)

        self.assertEqual(validated.displayCategoryCode, 80754)
        self.assertEqual(validated.notices[0].noticeCategoryName, "주방용품")

    def test_empty_notice_details_are_rejected(self):

        payload = dict(VALID_COUPANG_SELLER_FULFILLED_FIELDS)
        payload.update({
            "displayCategoryCode": 80754,
            "notices": [{
                "noticeCategoryName": "주방용품",
                "noticeCategoryDetailNames": [],
            }],
        })

        with self.assertRaises(ValidationError):
            CoupangSellerFulfilledFields.model_validate(payload)

    def test_shipping_and_return_placeholders_are_rejected(self):

        placeholder_fields = (
            "returnCenterCode", "outboundShippingPlaceCode", "returnChargeName",
            "companyContactNumber", "returnZipCode", "returnAddress",
            "returnAddressDetail",
        )
        for field_name in placeholder_fields:
            with self.subTest(field_name=field_name):
                payload = dict(VALID_COUPANG_SELLER_FULFILLED_FIELDS)
                payload[field_name] = f"<{field_name}>"
                with self.assertRaises(ValidationError):
                    CoupangSellerFulfilledFields.model_validate(payload)

    def test_rocket_growth_quantity_per_box_must_be_exactly_one(self):
        """공식 문서: 로켓그로스는 quantityPerBox가 항상 1이어야 한다."""

        payload = {
            "barcode": "8801234567890",
            "priceData": {"salePrice": "9000", "originalPrice": "10000"},
            "skuInfo": {
                "weight": "1.5", "width": "10", "length": "10",
                "height": "10", "fragile": False, "quantityPerBox": 2,
            },
            "distributionPeriod": 365,
            "expiredAtManaged": False,
            "rocketGrowthAdditionalInformation": {
                "rfmInboundName": "테스트", "legalAgreement": "AGREE",
            },
        }

        with self.assertRaises(ValidationError):
            CoupangRocketGrowthFields.model_validate(payload)

    def test_naver_schema_rejects_undocumented_secondary_source_fields(self):
        """
        네이버 공식 문서 접근 불가로 GitHub Discussion에서만 확인된
        salePrice/stockQuantity 등은 스키마에 아예 없으므로 extra="forbid"
        에 의해 자동으로 거부된다.
        """

        with self.assertRaises(ValidationError):
            NaverSellerFulfilledFields.model_validate(
                {"deliveryType": "DELIVERY", "salePrice": "9000"},
            )


class RequiredFieldsSelectionIntegrationTestCase(unittest.TestCase):
    """select_fulfillment_mode() 경로에서의 스키마 검증 통합 확인."""

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
            name="테스트 회사", business_number="666-66-66666", ceo="테스트",
            phone="02-000-0000", email="fields@example.com", address="서울",
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

    def _candidate(self) -> ProductCandidate:

        candidate = ProductCandidate(
            candidate_key="test:COUPANG:CAND-1", source_type="TREND",
            source_reference="CAND-1", market="COUPANG",
            product_name="테스트 상품", status=CandidateStatus.APPROVED,
        )
        self.db.add(candidate)
        self.db.commit()

        return candidate

    def _channel(self) -> MarketplaceChannel:

        channel = MarketplaceChannel(
            code="COUPANG", name="쿠팡", doc_verification_status="VERIFIED",
        )
        self.db.add(channel)
        self.db.commit()

        return channel

    def _account(self, channel_id: int) -> MarketplaceAccount:

        account = MarketplaceAccount(
            company_id=self.company_id, channel_id=channel_id,
            account_code="a1", account_name="계정",
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

    def test_select_fulfillment_mode_rejects_invalid_required_fields(self):

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

        with self.assertRaises(BadRequestException):
            self.listing_service.select_fulfillment_mode(
                MarketplaceFulfillmentSelectionCreateRequest(
                    listing_id=listing.id,
                    fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                    required_fields={"unsupported": "value"},
                    idempotency_key=_next_key("sel"),
                ),
                selected_by=1, company_id=self.company_id,
            )

    def test_select_fulfillment_mode_accepts_valid_required_fields(self):

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

        selection, _dup, _events = self.listing_service.select_fulfillment_mode(
            MarketplaceFulfillmentSelectionCreateRequest(
                listing_id=listing.id,
                fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                required_fields=VALID_COUPANG_SELLER_FULFILLED_FIELDS,
                idempotency_key=_next_key("sel"),
            ),
            selected_by=1, company_id=self.company_id,
        )

        self.assertEqual(
            selection.required_fields_schema_name, "coupang_seller_fulfilled",
        )
        self.assertEqual(selection.required_fields_schema_version, "1.0.0")
        self.assertEqual(len(selection.required_fields_fingerprint), 64)

    def _listing_with_valid_selection(self):

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

    def _approve(self, listing, selection):

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
        approved, _dup = self.approval_service.approve(
            pending.id,
            MarketplaceSubmissionApprovalDecisionRequest(
                expires_at=datetime.utcnow() + timedelta(days=1),
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

    def _reseed_policy_after_corruption(self, listing, selection):
        """
        승인 이후 required_fields_json을 직접 손상시키는 테스트 전용
        — 그 손상은 CA-1 채널 정책 지문(옵션/가격/배송 변경 감지)도
        함께 무효화하므로, 손상된 값을 그대로 반영해 정책 게이트만
        다시 통과시킨다(이 테스트들이 실제로 검증하려는 더 깊은
        required_fields Schema 재검증 실패 경로에 도달시키기 위함).
        """

        account = self.db.get(MarketplaceAccount, listing.marketplace_account_id)
        channel = self.db.get(MarketplaceChannel, account.channel_id)
        seed_channel_eligible_policy(
            self.db, company_id=self.company_id,
            product_candidate_id=listing.product_candidate_id,
            channel=channel.code, selection_id=selection.id,
        )

    # ----------------------------------------------------
    # 5) 저장된 JSON 손상 시 fail-closed
    # ----------------------------------------------------

    def test_corrupted_stored_required_fields_json_fails_closed_on_submit(
        self,
    ):

        listing, selection = self._listing_with_valid_selection()
        self._approve(listing, selection)

        # 저장소 손상을 직접 재현 — 유효하지 않은 JSON 문자열로 덮어쓴다.
        selection.required_fields_json = "{not-valid-json::"
        self.db.commit()
        self._reseed_policy_after_corruption(listing, selection)

        result = self.submission_service.submit(
            MarketplaceSubmissionRequest(
                listing_id=listing.id, selection_id=selection.id,
                idempotency_key=_next_key("sub"),
            ),
            self.company_id,
        )

        self.assertEqual(result.status, "FAILED")
        self.assertIn("손상", result.error_reason)

    def test_stored_json_no_longer_matching_schema_fails_closed_on_submit(
        self,
    ):
        """
        JSON 자체는 유효하지만(파싱 성공), 스키마 요구사항(필수 필드
        누락)을 더 이상 만족하지 못하는 손상 상황도 fail-closed여야
        한다.
        """

        listing, selection = self._listing_with_valid_selection()
        self._approve(listing, selection)

        broken = dict(VALID_COUPANG_SELLER_FULFILLED_FIELDS)
        del broken["items"]
        selection.required_fields_json = json.dumps(broken)
        self.db.commit()
        self._reseed_policy_after_corruption(listing, selection)

        result = self.submission_service.submit(
            MarketplaceSubmissionRequest(
                listing_id=listing.id, selection_id=selection.id,
                idempotency_key=_next_key("sub"),
            ),
            self.company_id,
        )

        self.assertEqual(result.status, "FAILED")
        self.assertIn("재검증 실패", result.error_reason)

    def test_stored_schema_name_mismatch_with_registry_fails_closed(self):
        """
        저장된 schema_name/version이 (레지스트리 업데이트 등으로) 지금
        레지스트리와 더 이상 일치하지 않으면 제출을 차단해야 한다.
        """

        listing, selection = self._listing_with_valid_selection()

        # fingerprint 자체가 schema_name/version을 포함하므로, 승인은
        # 손상 이후에 발급해 fingerprint는 일치시키되(승인 유효성 자체는
        # 통과) 레지스트리와의 schema 버전 불일치만 분리 검증한다.
        selection.required_fields_schema_version = "0.0.1-stale"
        self.db.commit()

        self._approve(listing, selection)

        result = self.submission_service.submit(
            MarketplaceSubmissionRequest(
                listing_id=listing.id, selection_id=selection.id,
                idempotency_key=_next_key("sub"),
            ),
            self.company_id,
        )

        self.assertEqual(result.status, "FAILED")
        self.assertIn("Schema 버전", result.error_reason)


if __name__ == "__main__":
    unittest.main()
