"""
=========================================================
Homez OS

File : tests/test_listing_wizard_service.py

Gate I(2026-08-08) — 상품등록 통합 마법사 Service 통합 검증. 실제
DB·외부 API 없음 — 전부 임시 SQLite + Fake 경로만 사용한다(기존
marketplace_listing 도메인 테스트와 동일한 패턴).

시나리오:
- 전체 happy path(1~10단계 → SUCCEEDED)
- 회사 격리(다른 회사 위저드 404)
- 버전 충돌(오래된 expected_version → 409)
- 사전검사 차단(이미지 없음/마진 음수)
- 승인 fingerprint 불일치 거부
- 승인 nonce 재사용 거부
- 복제가 승인/제출 이력을 물려받지 않음
- 부분 성공 + 실패 채널만 재시도
- idempotent 생성(같은 creation_idempotency_key)
=========================================================
"""

import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.core.exceptions import UnauthorizedException
from app.database.base import Base
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.automation_safety.service import SafetyService
from app.domains.company.model import Company
from app.domains.marketplace_listing.constants import CapabilityStatus
from app.domains.marketplace_listing.constants import FulfillmentMode
from app.domains.marketplace_listing.constants import WizardStatus
from app.domains.marketplace_listing.coupang_logistics_provider import (
    LogisticsLocation,
    cache_locations,
)
from app.domains.marketplace_listing.listing_wizard_revoke_nonce import (
    clear_all_wizard_revoke_nonces,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardRevokeApprovalRequest,
)
from app.domains.marketplace_listing.category_metadata import notice_input_fingerprint
from app.domains.marketplace_listing.listing_wizard_approval_nonce import (
    clear_all_wizard_approval_nonces,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    EconomicsInputItem,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    FulfillmentSelectionInput,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardApproveRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardChannelsUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardCloneRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardCreateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardDraftUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardEconomicsUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardFulfillmentUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardMediaUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardSourceUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardSubmitRequest,
)
from app.domains.marketplace_listing.listing_wizard_service import (
    ListingWizardService,
)
from app.domains.marketplace_listing.model import (
    ListingWizard,
)
from app.domains.marketplace_listing.model import (
    MarketplaceAccount,
)
from app.domains.marketplace_listing.model import (
    MarketplaceChannel,
)
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
from app.core.recent_auth import issue_recent_auth_token
from app.core.recent_auth import reset_recent_auth_state_for_tests
from app.domains.channel_policy.model import ChannelPolicyEvaluation
from app.domains.channel_policy.model import ChannelPolicyRule
from app.domains.channel_policy.model import CompanyChannelPolicySettings
from app.domains.channel_policy.service import ChannelPolicyService
from app.domains.media_asset.model import MediaAsset
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.user.model import User  # noqa: F401 (Company relationship 해석용)

_COUNTER = 0


def _key(prefix: str) -> str:

    global _COUNTER
    _COUNTER += 1
    return f"{prefix}-{_COUNTER}"


# Audit(2026-08-21, CTO 후속 지시) — required_fields(제출 Adapter용
# 가격·배송 Schema)와는 별개로, channel_policy 규칙이 요구하는 구조화
# 값(원산지·브랜드·구매옵션·상품식별번호·네이버 배송유형). 카탈로그가
# 이제 부팅마다 자동 시딩되므로(app/desktop/main.py) 이 값이 없으면
# COUPANG의 PROHIBITED_ORIGIN_COUNTRY/PRODUCT_IDENTIFICATION_REQUIRED
# 규칙이 CHANNEL_DATA_REQUIRED로 정직하게 제출을 막는다 — 이 상수는
# `tests/channel_policy_test_helpers.py::seed_channel_eligible_policy()`
# 와 동일한 값을 재사용해 "완전히 준비된 위저드 제출"을 나타낸다.
VALID_CHANNEL_POLICY_ATTRIBUTES = {
    "origin_country": "KOREA",
    "brand": "HOMEZ",
    "purchase_options": {"opt": "1"},
    "product_identifier": "TEST-SKU-001",
    "deliveryType": "DELIVERY",
    "deliveryAttributeType": "NORMAL",
    "official_category_code": "99999",
    "category_metadata_version": "test-meta-v1",
    "category_metadata_fingerprint": "test-meta-fingerprint",
    "notice_information": {"기타 재화::품명 및 모델명": "테스트 상품"},
    "notice_required_field_keys": ["기타 재화::품명 및 모델명"],
    "notice_confirmed_at": "2026-08-24T00:00:00",
    "notice_confirmed_by_user_id": 1,
    "notice_input_fingerprint": notice_input_fingerprint(
        "99999", "test-meta-v1", "test-meta-fingerprint",
        {"기타 재화::품명 및 모델명": "테스트 상품"},
    ),
}

# 2026-08-30 V7 안정화 Phase 1(감사 F-01) — 7단계 사전검사와 9~10단계
# 실제 Payload 생성이 이제 coupang_submission_contract.py 하나만
# 쓴다(둘이 다른 검사를 하던 것이 실제 Live 4회 제출 중 3회 실패의
# 근본 원인이었다). 그래서 "READY_FOR_APPROVAL"의 의미가 이전보다
# 엄격해졌다 — 이 fixture로 승인 가능한 위저드를 만드는 91개 기존
# 테스트(경제성/취소/버전충돌 등, Coupang 필수값 자체를 검증 대상으로
# 삼지 않는 테스트들) 전부가 이제 실제 Live 제출과 동일한 최소 유효
# 값을 필요로 한다 — 값을 비워 통과시키던 이전 상태가 바로 그 결함의
# 재발 지점이었으므로 여기서 되돌리지 않는다.
VALID_IMAGES = [{
    "imageOrder": 0, "imageType": "REPRESENTATION",
    "vendorPath": "https://images.example.test/product.png",
}]
VALID_NOTICES = [{
    "noticeCategoryName": "기타 재화",
    "noticeCategoryDetailNames": [{
        "noticeCategoryDetailName": "품명 및 모델명",
        "content": "테스트 상품",
    }],
}]
VALID_CONTENTS = [{
    "contentsType": "TEXT",
    "contentDetails": [{"content": "테스트 상품 상세 설명", "detailType": "TEXT"}],
}]

VALID_REQUIRED_FIELDS = {
    "deliveryMethod": "AGENT_BUY",
    "deliveryChargeType": "FREE",
    "deliveryCharge": "0",
    "deliveryChargeOnReturn": "2500",
    "returnCharge": "2500",
    "returnCenterCode": "RC001",
    "outboundShippingPlaceCode": "88001",
    "returnChargeName": "반품지",
    "companyContactNumber": "0212345678",
    "returnZipCode": "12345",
    "returnAddress": "서울시 강남구",
    "items": [{"itemName": "기본형", "externalVendorSku": "SKU-001"}],
    "originalPrice": "10000",
    "salePrice": "9000",
    "maximumBuyCount": 100,
    "displayCategoryCode": 99999,
    "images": VALID_IMAGES,
    "notices": VALID_NOTICES,
    "contents": VALID_CONTENTS,
    "liveImageRightsConfirmed": True,
    "vendorUserId": "sin945",
    "deliveryCompanyCode": "CJGLS",
    "brandState": "NO_BRAND",
    "brand": "HOMEZ",
}


class ListingWizardServiceTestCase(unittest.TestCase):

    def setUp(self):

        clear_all_wizard_approval_nonces()
        clear_all_wizard_revoke_nonces()
        reset_recent_auth_state_for_tests()

        # 실제 저장소에는 Gate H/I가 만든, 실제 homez.db에는 아직
        # 적용하지 않은 pending Migration 파일이 존재하므로(의도된
        # 상태), 이 프로세스에서 실제 경로로 재계산하면 항상 제한
        # 모드가 된다 — 이 테스트는 "위저드 사전검사가 제한 모드를
        # 올바르게 반영하는가"가 아니라 위저드 자체의 단계 흐름을
        # 검증하는 것이 목적이므로, 제한 모드 신호만 고정한다(그
        # 신호 자체의 정확성은 test_migration_restricted_mode.py가
        # 이미 전담 검증한다).
        self._restricted_mode_patcher = mock.patch(
            "app.domains.marketplace_listing.listing_wizard_precheck."
            "is_restricted_mode",
            return_value=False,
        )
        self._restricted_mode_patcher.start()
        self.addCleanup(self._restricted_mode_patcher.stop)

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
                ListingWizard.__table__,
                MediaAsset.__table__,
                AutomationModeState.__table__,
                EmergencyStop.__table__,
                ExecutionLimit.__table__,
                ExecutionUsage.__table__,
                ExecutionPeriodUsage.__table__,
                ChannelPolicyRule.__table__,
                CompanyChannelPolicySettings.__table__,
                ChannelPolicyEvaluation.__table__,
            ],
        )
        with self.engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE audit_logs ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "company_id INTEGER, user_id INTEGER, "
                "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
                "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
                "ip_address VARCHAR(50)"
                ")",
            ))

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()
        self.service = ListingWizardService(self.db)

        # Audit(2026-08-21, CTO 후속 지시) — evaluate_and_record()의
        # fail-open 결함 수정 이후, 이 채널(COUPANG)에 규칙 행이
        # 하나도 없으면 CHANNEL_DATA_REQUIRED로 fail-closed 처리된다.
        # 실제 부팅 시 자동 시딩되는 것과 동일하게, 이 테스트 DB에도
        # 진짜 카탈로그를 시딩해 실제 정책 엔진을 그대로 통과시킨다
        # (판정 로직을 흉내내지 않는다).
        ChannelPolicyService(self.db).seed_rule_catalog()

        company = Company(
            name="위저드 테스트 회사", business_number="444-44-44444",
            ceo="테스트", phone="02-000-0000", email="wiz@example.com",
            address="서울",
        )
        self.db.add(company)
        self.db.commit()
        self.company_id = company.id

        other_company = Company(
            name="다른 회사", business_number="555-55-55555",
            ceo="테스트2", phone="02-111-1111", email="wiz2@example.com",
            address="부산",
        )
        self.db.add(other_company)
        self.db.commit()
        self.other_company_id = other_company.id

        # 기본 AutomationMode는 RECOMMEND_ONLY(항상 DENY)라, 제출까지
        # 구조적으로 성공시키려면 명시적으로 전환해야 한다(기존 도메인의
        # 의도된 안전장치 — 이 사실 자체가 결함이 아니다).
        SafetyService(self.db).set_mode(
            AutomationMode.OPERATOR_APPROVAL, set_by=1, is_admin=True,
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        clear_all_wizard_approval_nonces()
        clear_all_wizard_revoke_nonces()

    # ---------------- 시딩 헬퍼 ----------------

    def _candidate(self, ref="CAND-1", status=CandidateStatus.APPROVED):

        candidate = ProductCandidate(
            candidate_key=f"wiz:COUPANG:{ref}", source_type="TREND",
            source_reference=ref, market="COUPANG",
            product_name="테스트 상품", status=status,
        )
        self.db.add(candidate)
        self.db.commit()
        return candidate

    def _channel(self, code="COUPANG", name="쿠팡"):

        channel = MarketplaceChannel(
            code=code, name=name, doc_verification_status="VERIFIED",
        )
        self.db.add(channel)
        self.db.commit()
        return channel

    def _account(self, channel_id, company_id=None, code="acct-1"):

        account = MarketplaceAccount(
            company_id=company_id or self.company_id, channel_id=channel_id,
            account_code=code, account_name="계정",
        )
        self.db.add(account)
        self.db.commit()
        return account

    def _capability(
        self, channel_id, mode=FulfillmentMode.SELLER_FULFILLED,
        schema_name="coupang_seller_fulfilled", schema_version="1.0.0",
        requires_eligibility_check=False,
    ):

        capability = MarketplaceFulfillmentCapability(
            channel_id=channel_id, fulfillment_mode=mode, is_supported=True,
            requires_eligibility_check=requires_eligibility_check,
            requires_account_contract=False,
            external_display_name=mode, policy_version="1.0.0",
            schema_name=schema_name, schema_version=schema_version,
            doc_source_reference="test", status=CapabilityStatus.VERIFIED,
            verified_at=datetime.utcnow(),
        )
        self.db.add(capability)
        self.db.commit()
        return capability

    def _media_asset(self, company_id=None, owner_id=1):

        asset = MediaAsset(
            company_id=company_id or self.company_id,
            owner_type="PRODUCT_CANDIDATE", owner_id=owner_id,
            asset_role="GENERATED", purpose="MAIN", display_order=0,
            storage_path=f"media/{_key('path')}.png",
            mime_type="image/png", file_size_bytes=1024,
            sha256_hex="a" * 64,
            # 2026-08-20 3차 지시로 rights_status 기본값이
            # RIGHTS_UNVERIFIED로 바뀌었다 — 이 테스트의 목적은 위저드
            # 흐름 자체이므로, 이미 사용권이 확인된 이미지라고 가정하고
            # 명시적으로 VERIFIED로 시딩한다(권리 게이트 자체의 검증은
            # tests/test_long_detail_image_ingest.py가 전담).
            rights_status="VERIFIED",
        )
        self.db.add(asset)
        self.db.commit()
        return asset

    def _full_setup(self):
        """candidate + channel + account + capability + media 1건."""

        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)
        media = self._media_asset(owner_id=candidate.id)
        return candidate, channel, account, media

    def _create_wizard(self, company_id=None) -> ListingWizard:

        wizard, _dup = self.service.create(
            WizardCreateRequest(creation_idempotency_key=_key("wiz")),
            created_by=1, company_id=company_id or self.company_id,
        )
        return wizard

    def _cache_coupang_logistics(self, wizard_id: int, company_id=None) -> None:
        """쿠팡 조회 API를 대신하는 테스트 전용 검증 완료 위치."""

        owner_company_id = company_id or self.company_id
        cache_locations(owner_company_id, wizard_id, "outbound", [
            LogisticsLocation(
                code="88001", name="테스트 출고지", usable=True,
            ),
        ])
        cache_locations(owner_company_id, wizard_id, "return", [
            LogisticsLocation(
                code="RET-TEST-1", name="테스트 반품지", usable=True,
                contact_number="02-0000-0000", zip_code="00000",
                address="테스트 주소", address_detail="테스트 상세주소",
            ),
        ])

    def _advance_to_ready_for_approval(self, candidate, account, media):

        wizard = self._create_wizard()

        wizard = self.service.update_source(
            wizard.id, self.company_id,
            WizardSourceUpdateRequest(
                expected_version=wizard.version,
                product_candidate_id=candidate.id,
            ),
        )
        wizard = self.service.update_draft(
            wizard.id, self.company_id,
            WizardDraftUpdateRequest(
                expected_version=wizard.version, product_name="테스트 상품",
                brand="HOMEZ", category="생활용품",
            ),
        )
        wizard = self.service.update_media(
            wizard.id, self.company_id,
            WizardMediaUpdateRequest(
                expected_version=wizard.version,
                selected_media_asset_ids=[media.id],
            ),
        )
        wizard = self.service.update_channels(
            wizard.id, self.company_id,
            WizardChannelsUpdateRequest(
                expected_version=wizard.version,
                marketplace_account_ids=[account.id],
            ),
        )
        self._cache_coupang_logistics(wizard.id)
        wizard = self.service.update_fulfillment(
            wizard.id, self.company_id,
            WizardFulfillmentUpdateRequest(
                expected_version=wizard.version,
                selections=[FulfillmentSelectionInput(
                    marketplace_account_id=account.id,
                    fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                    outbound_shipping_place_code="88001",
                    return_center_code="RET-TEST-1",
                    required_fields=VALID_REQUIRED_FIELDS,
                    channel_policy_attributes=VALID_CHANNEL_POLICY_ATTRIBUTES,
                )],
            ),
        )
        wizard = self.service.update_economics(
            wizard.id, self.company_id,
            WizardEconomicsUpdateRequest(
                expected_version=wizard.version,
                items=[EconomicsInputItem(
                    marketplace_account_id=account.id,
                    cost_of_goods="5000", sale_price="9000",
                    channel_fee_rate="0.1",
                )],
            ),
        )
        result = self.service.validate(
            wizard.id, self.company_id, wizard.version,
        )
        self.assertEqual(result.status, "READY_FOR_APPROVAL", result.issues)

        return self.service.get(wizard.id, self.company_id)

    def test_coupang_structured_charges_payload_reaches_ready_for_approval(self):
        """
        2026-08-29 — 5단계 배송비·반품비 구조화 UI(원시 JSON 입력
        대신 한국어 구조화 입력) 도입. 이 테스트는 그 UI(app/web/
        console.js의 lwSaveCurrentStep)가 실제로 만들어 보낼 required_
        fields의 정확한 모양을 그대로 재현해, 실제 Schema 검증
        (CoupangSellerFulfilledFields)과 사전검사를 그대로 통과하는지
        확인한다 — 화면 자체는 이 테스트로 구동하지 않는다(그건
        별도의 브라우저 검증 몫), 여기서는 "화면이 만들 payload
        모양이 서버 계약과 맞는가"만 집중 검증한다.

        deliveryMethod는 화면에서 사용자가 입력하지 않고 항상
        SEQUENCIAL로 자동 결정된다. originalPrice/salePrice(상품
        전체)는 옵션 조합표의 첫 행 값을 그대로 미러링한다(화면
        로직과 동일). 배송비·반품비(deliveryCharge 등)는 실제 값이
        아직 확정되지 않았으므로 이 테스트에서만 쓰는 합성값이다 —
        실제 제출에는 사용자가 화면에서 직접 입력한 값이 쓰인다.
        """

        candidate, _channel, account, media = self._full_setup()
        wizard = self._create_wizard()
        wizard = self.service.update_source(
            wizard.id, self.company_id,
            WizardSourceUpdateRequest(
                expected_version=wizard.version,
                product_candidate_id=candidate.id,
            ),
        )
        wizard = self.service.update_draft(
            wizard.id, self.company_id,
            WizardDraftUpdateRequest(
                expected_version=wizard.version, product_name="테스트 상품",
                brand="HOMEZ", category="생활용품",
            ),
        )
        wizard = self.service.update_media(
            wizard.id, self.company_id,
            WizardMediaUpdateRequest(
                expected_version=wizard.version,
                selected_media_asset_ids=[media.id],
            ),
        )
        wizard = self.service.update_channels(
            wizard.id, self.company_id,
            WizardChannelsUpdateRequest(
                expected_version=wizard.version,
                marketplace_account_ids=[account.id],
            ),
        )
        self._cache_coupang_logistics(wizard.id)

        # 화면의 옵션 조합표(구매옵션 조합 UI)가 만드는 items[] 행 —
        # 실제 확인된 테스트 상품 값(38x38cm / 40매, SKU, 1회 최대
        # 구매수량 10, 정상가·판매가 12900원).
        combo_items = [{
            "itemName": "38x38cm / 40매",
            "externalVendorSku": "HOMEZ-DISHCLOTH-40-V1",
            "originalPrice": "12900",
            "salePrice": "12900",
            "maximumBuyCount": 10,
            "unitCount": 40,
            "optionAttributes": {"규격": "38x38cm", "수량": "40매"},
        }]
        # lwSaveCurrentStep()과 동일한 순서로 조립 — deliveryMethod는
        # 자동(SEQUENCIAL), originalPrice/salePrice는 items[0]을
        # 그대로 미러링, 배송비·반품비 4개는 화면에서 사용자가 직접
        # 입력하는 값(여기서는 합성 테스트값).
        required_fields = {
            **VALID_REQUIRED_FIELDS,
            "deliveryMethod": "SEQUENCIAL",
            "deliveryChargeType": "NOT_FREE",
            "deliveryCharge": "3000",
            "deliveryChargeOnReturn": "3000",
            "returnCharge": "3000",
            "maximumBuyCount": 10,
            "items": combo_items,
            "originalPrice": combo_items[0]["originalPrice"],
            "salePrice": combo_items[0]["salePrice"],
        }

        wizard = self.service.update_fulfillment(
            wizard.id, self.company_id,
            WizardFulfillmentUpdateRequest(
                expected_version=wizard.version,
                selections=[FulfillmentSelectionInput(
                    marketplace_account_id=account.id,
                    fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                    outbound_shipping_place_code="88001",
                    return_center_code="RET-TEST-1",
                    required_fields=required_fields,
                    channel_policy_attributes=VALID_CHANNEL_POLICY_ATTRIBUTES,
                )],
            ),
        )
        wizard = self.service.update_economics(
            wizard.id, self.company_id,
            WizardEconomicsUpdateRequest(
                expected_version=wizard.version,
                items=[EconomicsInputItem(
                    marketplace_account_id=account.id,
                    cost_of_goods="5000", sale_price="12900",
                    channel_fee_rate="0.1",
                )],
            ),
        )
        result = self.service.validate(
            wizard.id, self.company_id, wizard.version,
        )
        issue_codes = [issue.code for issue in result.issues]
        self.assertNotIn("FULFILLMENT_REQUIRED_FIELDS_INVALID", issue_codes)
        self.assertEqual(result.status, "READY_FOR_APPROVAL", result.issues)

    def _recent_auth_token(self, user_id=99) -> str:

        token, _expires_at = issue_recent_auth_token(user_id)
        return token

    def test_update_economics_blocks_when_profitability_capability_deactivated(self):
        """Audit(2026-08-21, CTO 후속 지시) — AI Capability Registry
        실연결 증거(위저드 6단계 마진 미리보기)."""

        from app.domains.ai_governance.service import InactiveCapabilityError
        from tests.ai_governance_test_helpers import deactivated_capability

        wizard = self._create_wizard()

        with deactivated_capability("PROFITABILITY_CALCULATION"):
            with self.assertRaises(InactiveCapabilityError):
                self.service.update_economics(
                    wizard.id, self.company_id,
                    WizardEconomicsUpdateRequest(
                        expected_version=wizard.version,
                        items=[EconomicsInputItem(
                            marketplace_account_id=1,
                            cost_of_goods="5000", sale_price="9000",
                        )],
                    ),
                )

    # ---------------- happy path ----------------

    def test_full_wizard_flow_reaches_succeeded(self):

        candidate, channel, account, media = self._full_setup()
        wizard = self._advance_to_ready_for_approval(candidate, account, media)

        preview = self.service.approval_preview(wizard.id, self.company_id)
        self.assertTrue(preview.fingerprint)
        self.assertTrue(preview.approval_nonce)

        approved = self.service.approve(
            wizard.id, self.company_id, approved_by=99,
            recent_auth_token=self._recent_auth_token(99),
            data=WizardApproveRequest(
                expected_version=preview.version,
                approval_nonce=preview.approval_nonce,
                expected_fingerprint=preview.fingerprint,
                product_image_match_confirmed=True,
            ),
        )
        self.assertEqual(approved.status, WizardStatus.APPROVED)
        self.assertEqual(approved.approved_by_user_id, 99)

        submitted = self.service.submit(
            wizard.id, self.company_id, approved_by=99,
            data=WizardSubmitRequest(
                expected_version=approved.version, execution_mode="SUBMIT",
            ),
        )
        self.assertEqual(submitted.status, WizardStatus.SUCCEEDED)

        results = self.service.results(wizard.id, self.company_id)
        self.assertEqual(len(results.channels), 1)
        # MarketplaceSubmission의 "구조적으로 성공"은 SUBMITTED가
        # 아니라 PENDING이다 — 이 코드베이스에 실제 외부 네트워크
        # 호출이 없다는 기존 계약과 동일한 이유(ListingStatus 문서
        # 참고).
        self.assertEqual(results.channels[0].status, "PENDING")
        self.assertIsNotNone(results.channels[0].listing_id)

    def test_approval_package_never_contains_credential_shaped_content(self):
        """
        승인 패키지 fingerprint 입력에는 Credential·locale·번역 문구가
        전혀 없어야 한다(Gate I 설계 5번 항목 "제외" 목록) — 이 위저드
        경로 어디에도 Credential을 저장하는 컬럼 자체가 없으므로 구조적
        으로 불가능하지만, 회귀 방지를 위해 패키지 전체를 문자열로
        스캔해 흔한 credential 키가 전혀 없는지 확인한다.
        """

        candidate, channel, account, media = self._full_setup()
        wizard = self._advance_to_ready_for_approval(candidate, account, media)
        preview = self.service.approval_preview(wizard.id, self.company_id)

        package_text = json.dumps(preview.approval_package).lower()
        for forbidden in (
            "password", "api_key", "apikey", "secret", "access_key",
            "credential", "recent-auth", "approval_nonce",
        ):
            self.assertNotIn(forbidden, package_text)

        # locale/번역 문구도 fingerprint 입력에 포함되지 않는다(item 5
        # "제외" 목록) — 화면 표시 순서 등 UI 전용 값도 마찬가지.
        self.assertNotIn("ko-kr", package_text)
        self.assertNotIn("en-us", package_text)

    # ---------------- 회사 격리 ----------------

    def test_cross_company_get_returns_not_found(self):

        wizard = self._create_wizard(company_id=self.company_id)

        with self.assertRaises(NotFoundException):
            self.service.get(wizard.id, self.other_company_id)

    # ---------------- 버전 충돌 ----------------

    def test_stale_version_patch_raises_conflict(self):

        wizard = self._create_wizard()
        candidate = self._candidate()
        # 별도 정수 변수로 스냅샷을 떠 둔다 — 같은 Session을 계속 쓰는
        # 이 테스트 프로세스에서는 ORM의 bulk UPDATE 동기화가 이미
        # 세션에 올라온 wizard 객체의 .version 속성을 그 자리에서 계속
        # 최신값으로 맞춰 버리므로(실서비스에서는 요청마다 새 Session을
        # 쓰므로 이런 자동 동기화가 없다), 매번 wizard.version을 다시
        # 읽으면 "오래된 버전"을 재현할 수 없다.
        stale_version = wizard.version

        self.service.update_source(
            wizard.id, self.company_id,
            WizardSourceUpdateRequest(
                expected_version=stale_version,
                product_candidate_id=candidate.id,
            ),
        )

        with self.assertRaises(ConflictException):
            self.service.update_source(
                wizard.id, self.company_id,
                WizardSourceUpdateRequest(
                    expected_version=stale_version,
                    product_candidate_id=candidate.id,
                ),
            )

    def test_version_conflict_detail_is_structured_with_error_code(self):
        """
        Gate J(2026-08-08) — 버전 충돌은 문자열이 아니라 구조화된
        dict(error_code=WIZARD_VERSION_CONFLICT + 현재 버전/단계/
        상태/마지막 저장 토큰/시각)를 반환해, 클라이언트가 별도 GET
        없이 재조회→재적용 흐름을 시작할 수 있어야 한다.
        """

        candidate = self._candidate()
        wizard = self._create_wizard()
        stale_version = wizard.version

        updated = self.service.update_source(
            wizard.id, self.company_id,
            WizardSourceUpdateRequest(
                expected_version=stale_version,
                product_candidate_id=candidate.id,
                autosave_client_token="tab-A",
            ),
        )

        with self.assertRaises(ConflictException) as ctx:
            self.service.update_source(
                wizard.id, self.company_id,
                WizardSourceUpdateRequest(
                    expected_version=stale_version,
                    product_candidate_id=candidate.id,
                    autosave_client_token="tab-B",
                ),
            )

        detail = ctx.exception.detail
        self.assertIsInstance(detail, dict)
        self.assertEqual(detail["error_code"], "WIZARD_VERSION_CONFLICT")
        self.assertEqual(detail["current_version"], updated.version)
        self.assertEqual(detail["current_step"], updated.current_step)
        self.assertEqual(detail["status"], updated.status)
        # 마지막으로 실제 저장에 성공한 클라이언트(tab-A)가 그대로
        # 노출돼야 한다 — tab-B는 이 정보를 보고 "다른 탭에서 이미
        # 저장했다"는 것을 알 수 있다(중복 편집 감지).
        self.assertEqual(detail["autosave_client_token"], "tab-A")

    # ---------------- Gate J: 자동 저장 메타데이터 ----------------

    def test_autosave_client_token_and_timestamp_persisted_on_save(self):

        candidate = self._candidate()
        wizard = self._create_wizard()

        self.assertIsNone(wizard.autosave_client_token)
        self.assertIsNone(wizard.autosave_saved_at)

        updated = self.service.update_source(
            wizard.id, self.company_id,
            WizardSourceUpdateRequest(
                expected_version=wizard.version,
                product_candidate_id=candidate.id,
                autosave_client_token="tab-xyz",
            ),
        )

        self.assertEqual(updated.autosave_client_token, "tab-xyz")
        self.assertIsNotNone(updated.autosave_saved_at)

    def test_save_without_autosave_token_leaves_metadata_untouched(self):

        candidate = self._candidate()
        wizard = self._create_wizard()

        first = self.service.update_source(
            wizard.id, self.company_id,
            WizardSourceUpdateRequest(
                expected_version=wizard.version,
                product_candidate_id=candidate.id,
                autosave_client_token="tab-xyz",
            ),
        )
        self.assertEqual(first.autosave_client_token, "tab-xyz")
        first_saved_at = first.autosave_saved_at

        second = self.service.update_draft(
            wizard.id, self.company_id,
            WizardDraftUpdateRequest(
                expected_version=first.version, product_name="상품",
            ),
        )

        # autosave_client_token을 보내지 않은 저장은 이전에 기록된
        # 자동 저장 메타데이터를 건드리지 않는다(지워지지도, 갱신되지도
        # 않는다) — "마지막으로 자동 저장한 클라이언트가 누구였는가"
        # 라는 사실 자체는 이 저장으로 사라지지 않아야 한다.
        self.assertEqual(second.autosave_client_token, "tab-xyz")
        self.assertEqual(second.autosave_saved_at, first_saved_at)

    def test_draft_json_never_contains_autosave_token_field(self):
        """autosave_client_token은 draft 내용(JSON)에 섞여 들어가면
        안 된다 — 별도 컬럼으로만 저장된다."""

        candidate = self._candidate()
        wizard = self._create_wizard()
        wizard = self.service.update_source(
            wizard.id, self.company_id,
            WizardSourceUpdateRequest(
                expected_version=wizard.version,
                product_candidate_id=candidate.id,
            ),
        )

        updated = self.service.update_draft(
            wizard.id, self.company_id,
            WizardDraftUpdateRequest(
                expected_version=wizard.version, product_name="상품",
                autosave_client_token="tab-xyz",
            ),
        )

        self.assertNotIn("autosave_client_token", updated.draft_json)

    # ---------------- 사전검사 차단 ----------------

    def test_validate_blocks_when_no_media_selected(self):

        candidate = self._candidate()
        wizard = self._create_wizard()
        wizard = self.service.update_source(
            wizard.id, self.company_id,
            WizardSourceUpdateRequest(
                expected_version=wizard.version,
                product_candidate_id=candidate.id,
            ),
        )
        wizard = self.service.update_draft(
            wizard.id, self.company_id,
            WizardDraftUpdateRequest(
                expected_version=wizard.version, product_name="상품",
            ),
        )

        result = self.service.validate(
            wizard.id, self.company_id, wizard.version,
        )
        self.assertEqual(result.status, "NEEDS_CORRECTION")
        self.assertTrue(any(
            issue.code == "MEDIA_NONE_SELECTED" for issue in result.issues
        ))

        wizard = self.service.get(wizard.id, self.company_id)
        self.assertEqual(wizard.status, WizardStatus.NEEDS_CORRECTION)

    def test_validate_blocks_on_negative_margin(self):

        candidate, channel, account, media = self._full_setup()
        wizard = self._create_wizard()
        wizard = self.service.update_source(
            wizard.id, self.company_id,
            WizardSourceUpdateRequest(
                expected_version=wizard.version,
                product_candidate_id=candidate.id,
            ),
        )
        wizard = self.service.update_draft(
            wizard.id, self.company_id,
            WizardDraftUpdateRequest(
                expected_version=wizard.version, product_name="상품",
            ),
        )
        wizard = self.service.update_media(
            wizard.id, self.company_id,
            WizardMediaUpdateRequest(
                expected_version=wizard.version,
                selected_media_asset_ids=[media.id],
            ),
        )
        wizard = self.service.update_channels(
            wizard.id, self.company_id,
            WizardChannelsUpdateRequest(
                expected_version=wizard.version,
                marketplace_account_ids=[account.id],
            ),
        )
        self._cache_coupang_logistics(wizard.id)
        wizard = self.service.update_fulfillment(
            wizard.id, self.company_id,
            WizardFulfillmentUpdateRequest(
                expected_version=wizard.version,
                selections=[FulfillmentSelectionInput(
                    marketplace_account_id=account.id,
                    fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                    outbound_shipping_place_code="88001",
                    return_center_code="RET-TEST-1",
                    required_fields=VALID_REQUIRED_FIELDS,
                )],
            ),
        )
        wizard = self.service.update_economics(
            wizard.id, self.company_id,
            WizardEconomicsUpdateRequest(
                expected_version=wizard.version,
                items=[EconomicsInputItem(
                    marketplace_account_id=account.id,
                    cost_of_goods="50000", sale_price="9000",
                )],
            ),
        )

        result = self.service.validate(
            wizard.id, self.company_id, wizard.version,
        )
        self.assertEqual(result.status, "NEEDS_CORRECTION")
        self.assertTrue(any(
            issue.code == "ECONOMICS_NEGATIVE_MARGIN" for issue in result.issues
        ))

    def test_coupang_fulfillment_rejects_unverified_logistics_codes(self):
        candidate, _channel, account, media = self._full_setup()
        wizard = self._create_wizard()
        wizard = self.service.update_source(
            wizard.id, self.company_id,
            WizardSourceUpdateRequest(expected_version=wizard.version, product_candidate_id=candidate.id),
        )
        wizard = self.service.update_draft(
            wizard.id, self.company_id,
            WizardDraftUpdateRequest(expected_version=wizard.version, product_name="상품"),
        )
        wizard = self.service.update_media(
            wizard.id, self.company_id,
            WizardMediaUpdateRequest(expected_version=wizard.version, selected_media_asset_ids=[media.id]),
        )
        wizard = self.service.update_channels(
            wizard.id, self.company_id,
            WizardChannelsUpdateRequest(expected_version=wizard.version, marketplace_account_ids=[account.id]),
        )
        with self.assertRaisesRegex(Exception, "COUPANG_LOGISTICS_SELECTION_REQUIRED"):
            self.service.update_fulfillment(
                wizard.id, self.company_id,
                WizardFulfillmentUpdateRequest(
                    expected_version=wizard.version,
                    selections=[FulfillmentSelectionInput(
                        marketplace_account_id=account.id,
                        fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                        outbound_shipping_place_code="FORGED-OUT",
                        return_center_code="FORGED-RETURN",
                        required_fields=VALID_REQUIRED_FIELDS,
                    )],
                ),
            )

    def test_fulfillment_rejects_missing_required_exposed_purchase_option(self):
        # 2026-08-29 쿠팡 상품등록 핵심 차단 해결 — 실제 백업 DB
        # 증거(Wizard #6, 2026-08-26)에서 구매옵션이 Category
        # Metadata와 무관한 자유 텍스트로 저장된 결함(V7-COUPANG-
        # ATTR-001)의 저장 시점 재발 방지 테스트.
        candidate, _channel, account, media = self._full_setup()
        wizard = self._create_wizard()
        wizard = self.service.update_source(
            wizard.id, self.company_id,
            WizardSourceUpdateRequest(expected_version=wizard.version, product_candidate_id=candidate.id),
        )
        wizard = self.service.update_draft(
            wizard.id, self.company_id,
            WizardDraftUpdateRequest(expected_version=wizard.version, product_name="상품"),
        )
        wizard = self.service.update_media(
            wizard.id, self.company_id,
            WizardMediaUpdateRequest(expected_version=wizard.version, selected_media_asset_ids=[media.id]),
        )
        wizard = self.service.update_channels(
            wizard.id, self.company_id,
            WizardChannelsUpdateRequest(expected_version=wizard.version, marketplace_account_ids=[account.id]),
        )
        self._cache_coupang_logistics(wizard.id)
        attrs = dict(VALID_CHANNEL_POLICY_ATTRIBUTES)
        attrs["purchase_option_field_definitions"] = [{
            "attribute_type_name": "색상", "input_type": "SELECT",
            "input_values": ["레드", "블루"], "required": True, "exposed": True,
        }]
        attrs["purchase_options"] = {}
        with self.assertRaisesRegex(Exception, "COUPANG_PURCHASE_OPTION_REQUIRED"):
            self.service.update_fulfillment(
                wizard.id, self.company_id,
                WizardFulfillmentUpdateRequest(
                    expected_version=wizard.version,
                    selections=[FulfillmentSelectionInput(
                        marketplace_account_id=account.id,
                        fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                        outbound_shipping_place_code="88001",
                        return_center_code="RET-TEST-1",
                        required_fields=VALID_REQUIRED_FIELDS,
                        channel_policy_attributes=attrs,
                    )],
                ),
            )

    def test_fulfillment_accepts_valid_exposed_purchase_option(self):
        candidate, _channel, account, media = self._full_setup()
        wizard = self._create_wizard()
        wizard = self.service.update_source(
            wizard.id, self.company_id,
            WizardSourceUpdateRequest(expected_version=wizard.version, product_candidate_id=candidate.id),
        )
        wizard = self.service.update_draft(
            wizard.id, self.company_id,
            WizardDraftUpdateRequest(expected_version=wizard.version, product_name="상품"),
        )
        wizard = self.service.update_media(
            wizard.id, self.company_id,
            WizardMediaUpdateRequest(expected_version=wizard.version, selected_media_asset_ids=[media.id]),
        )
        wizard = self.service.update_channels(
            wizard.id, self.company_id,
            WizardChannelsUpdateRequest(expected_version=wizard.version, marketplace_account_ids=[account.id]),
        )
        self._cache_coupang_logistics(wizard.id)
        attrs = dict(VALID_CHANNEL_POLICY_ATTRIBUTES)
        attrs["purchase_option_field_definitions"] = [{
            "attribute_type_name": "색상", "input_type": "SELECT",
            "input_values": ["레드", "블루"], "required": True, "exposed": True,
        }]
        attrs["purchase_options"] = {"색상": "레드"}
        wizard = self.service.update_fulfillment(
            wizard.id, self.company_id,
            WizardFulfillmentUpdateRequest(
                expected_version=wizard.version,
                selections=[FulfillmentSelectionInput(
                    marketplace_account_id=account.id,
                    fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                    outbound_shipping_place_code="88001",
                    return_center_code="RET-TEST-1",
                    required_fields=VALID_REQUIRED_FIELDS,
                    channel_policy_attributes=attrs,
                )],
            ),
        )
        saved = json.loads(wizard.channel_selections_json)[0]["channel_policy_attributes"]
        self.assertEqual(saved["purchase_options"], {"색상": "레드"})

    def test_fulfillment_accepts_multi_option_items_with_per_item_attributes(self):
        # 2026-08-29 — Section 3 필수 시나리오 2번(다중 옵션 정상
        # 등록)의 저장 시점 검증. items[] 각 행이 자기 자신의
        # optionAttributes를 가질 때, 둘 다 유효하면 저장이 성공해야
        # 한다.
        candidate, _channel, account, media = self._full_setup()
        wizard = self._create_wizard()
        wizard = self.service.update_source(
            wizard.id, self.company_id,
            WizardSourceUpdateRequest(expected_version=wizard.version, product_candidate_id=candidate.id),
        )
        wizard = self.service.update_draft(
            wizard.id, self.company_id,
            WizardDraftUpdateRequest(expected_version=wizard.version, product_name="상품"),
        )
        wizard = self.service.update_media(
            wizard.id, self.company_id,
            WizardMediaUpdateRequest(expected_version=wizard.version, selected_media_asset_ids=[media.id]),
        )
        wizard = self.service.update_channels(
            wizard.id, self.company_id,
            WizardChannelsUpdateRequest(expected_version=wizard.version, marketplace_account_ids=[account.id]),
        )
        self._cache_coupang_logistics(wizard.id)
        attrs = dict(VALID_CHANNEL_POLICY_ATTRIBUTES)
        attrs["purchase_option_field_definitions"] = [{
            "attribute_type_name": "색상", "input_type": "SELECT",
            "input_values": ["레드", "블루"], "required": True, "exposed": True,
        }]
        multi_item_fields = dict(VALID_REQUIRED_FIELDS)
        multi_item_fields["items"] = [
            {"itemName": "레드", "externalVendorSku": "MULTI-SKU-RED", "optionAttributes": {"색상": "레드"}},
            {"itemName": "블루", "externalVendorSku": "MULTI-SKU-BLUE", "optionAttributes": {"색상": "블루"}},
        ]
        wizard = self.service.update_fulfillment(
            wizard.id, self.company_id,
            WizardFulfillmentUpdateRequest(
                expected_version=wizard.version,
                selections=[FulfillmentSelectionInput(
                    marketplace_account_id=account.id,
                    fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                    outbound_shipping_place_code="88001",
                    return_center_code="RET-TEST-1",
                    required_fields=multi_item_fields,
                    channel_policy_attributes=attrs,
                )],
            ),
        )
        saved_items = json.loads(wizard.channel_selections_json)[0]["required_fields"]["items"]
        self.assertEqual(len(saved_items), 2)
        self.assertEqual(saved_items[0]["optionAttributes"], {"색상": "레드"})
        self.assertEqual(saved_items[1]["optionAttributes"], {"색상": "블루"})

    def test_revisiting_channels_step_preserves_saved_fulfillment_data(self):
        # 2026-08-29 쿠팡 상품등록 핵심 차단 해결 — 격리 브라우저 E2E
        # 중 실제로 재현된 데이터 손실 결함. 5단계(판매 방식)까지
        # 저장을 마친 뒤 4단계(판매채널)로 되돌아가 동일한 계정
        # 목록으로 다시 "다음"을 누르면(선택을 바꾸지 않아도)
        # fulfillment_mode/required_fields/channel_policy_attributes가
        # 통째로 사라지던 것을 재현하고, 고침을 검증한다.
        candidate, _channel, account, media = self._full_setup()
        wizard = self._create_wizard()
        wizard = self.service.update_source(
            wizard.id, self.company_id,
            WizardSourceUpdateRequest(expected_version=wizard.version, product_candidate_id=candidate.id),
        )
        wizard = self.service.update_draft(
            wizard.id, self.company_id,
            WizardDraftUpdateRequest(expected_version=wizard.version, product_name="상품"),
        )
        wizard = self.service.update_media(
            wizard.id, self.company_id,
            WizardMediaUpdateRequest(expected_version=wizard.version, selected_media_asset_ids=[media.id]),
        )
        wizard = self.service.update_channels(
            wizard.id, self.company_id,
            WizardChannelsUpdateRequest(expected_version=wizard.version, marketplace_account_ids=[account.id]),
        )
        self._cache_coupang_logistics(wizard.id)
        attrs = dict(VALID_CHANNEL_POLICY_ATTRIBUTES)
        wizard = self.service.update_fulfillment(
            wizard.id, self.company_id,
            WizardFulfillmentUpdateRequest(
                expected_version=wizard.version,
                selections=[FulfillmentSelectionInput(
                    marketplace_account_id=account.id,
                    fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                    outbound_shipping_place_code="88001",
                    return_center_code="RET-TEST-1",
                    required_fields=VALID_REQUIRED_FIELDS,
                    channel_policy_attributes=attrs,
                )],
            ),
        )
        saved_before = json.loads(wizard.channel_selections_json)[0]
        self.assertEqual(saved_before["fulfillment_mode"], FulfillmentMode.SELLER_FULFILLED)

        # 사용자가 4단계로 돌아가 같은 계정 목록 그대로 "다음"을 다시
        # 누른 상황을 재현한다.
        wizard = self.service.update_channels(
            wizard.id, self.company_id,
            WizardChannelsUpdateRequest(expected_version=wizard.version, marketplace_account_ids=[account.id]),
        )
        saved_after = json.loads(wizard.channel_selections_json)[0]
        self.assertEqual(saved_after["fulfillment_mode"], FulfillmentMode.SELLER_FULFILLED)
        self.assertEqual(saved_after["required_fields"], saved_before["required_fields"])
        self.assertEqual(
            saved_after["channel_policy_attributes"], saved_before["channel_policy_attributes"],
        )

    def test_revisiting_channels_step_drops_deselected_account_data(self):
        # 병합 고침이 "선택 해제된 계정의 데이터는 사라진다"는 기존
        # 의도된 동작까지 깨뜨리지 않았는지 확인한다.
        candidate, channel, account, media = self._full_setup()
        account2 = self._account(channel.id, code="acct-2")
        wizard = self._create_wizard()
        wizard = self.service.update_source(
            wizard.id, self.company_id,
            WizardSourceUpdateRequest(expected_version=wizard.version, product_candidate_id=candidate.id),
        )
        wizard = self.service.update_draft(
            wizard.id, self.company_id,
            WizardDraftUpdateRequest(expected_version=wizard.version, product_name="상품"),
        )
        wizard = self.service.update_media(
            wizard.id, self.company_id,
            WizardMediaUpdateRequest(expected_version=wizard.version, selected_media_asset_ids=[media.id]),
        )
        wizard = self.service.update_channels(
            wizard.id, self.company_id,
            WizardChannelsUpdateRequest(
                expected_version=wizard.version, marketplace_account_ids=[account.id],
            ),
        )
        self._cache_coupang_logistics(wizard.id)
        wizard = self.service.update_fulfillment(
            wizard.id, self.company_id,
            WizardFulfillmentUpdateRequest(
                expected_version=wizard.version,
                selections=[FulfillmentSelectionInput(
                    marketplace_account_id=account.id,
                    fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                    outbound_shipping_place_code="88001",
                    return_center_code="RET-TEST-1",
                    required_fields=VALID_REQUIRED_FIELDS,
                )],
            ),
        )
        wizard = self.service.update_channels(
            wizard.id, self.company_id,
            WizardChannelsUpdateRequest(
                expected_version=wizard.version, marketplace_account_ids=[account2.id],
            ),
        )
        saved = json.loads(wizard.channel_selections_json)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0], {"marketplace_account_id": account2.id})

    def test_fulfillment_rejects_duplicate_option_combination(self):
        candidate, _channel, account, media = self._full_setup()
        wizard = self._create_wizard()
        wizard = self.service.update_source(
            wizard.id, self.company_id,
            WizardSourceUpdateRequest(expected_version=wizard.version, product_candidate_id=candidate.id),
        )
        wizard = self.service.update_draft(
            wizard.id, self.company_id,
            WizardDraftUpdateRequest(expected_version=wizard.version, product_name="상품"),
        )
        wizard = self.service.update_media(
            wizard.id, self.company_id,
            WizardMediaUpdateRequest(expected_version=wizard.version, selected_media_asset_ids=[media.id]),
        )
        wizard = self.service.update_channels(
            wizard.id, self.company_id,
            WizardChannelsUpdateRequest(expected_version=wizard.version, marketplace_account_ids=[account.id]),
        )
        self._cache_coupang_logistics(wizard.id)
        attrs = dict(VALID_CHANNEL_POLICY_ATTRIBUTES)
        attrs["purchase_option_field_definitions"] = [{
            "attribute_type_name": "색상", "input_type": "SELECT",
            "input_values": ["레드", "블루"], "required": True, "exposed": True,
        }]
        dup_fields = dict(VALID_REQUIRED_FIELDS)
        dup_fields["items"] = [
            {"itemName": "레드1", "externalVendorSku": "DUP-SKU-1", "optionAttributes": {"색상": "레드"}},
            {"itemName": "레드2", "externalVendorSku": "DUP-SKU-2", "optionAttributes": {"색상": "레드"}},
        ]
        with self.assertRaisesRegex(Exception, "COUPANG_DUPLICATE_OPTION_COMBINATION"):
            self.service.update_fulfillment(
                wizard.id, self.company_id,
                WizardFulfillmentUpdateRequest(
                    expected_version=wizard.version,
                    selections=[FulfillmentSelectionInput(
                        marketplace_account_id=account.id,
                        fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                        outbound_shipping_place_code="88001",
                        return_center_code="RET-TEST-1",
                        required_fields=dup_fields,
                        channel_policy_attributes=attrs,
                    )],
                ),
            )

    def test_coupang_fulfillment_reconstructs_private_fields_server_side(self):
        candidate, _channel, account, media = self._full_setup()
        wizard = self._create_wizard()
        wizard = self.service.update_source(
            wizard.id, self.company_id,
            WizardSourceUpdateRequest(expected_version=wizard.version, product_candidate_id=candidate.id),
        )
        wizard = self.service.update_draft(
            wizard.id, self.company_id,
            WizardDraftUpdateRequest(expected_version=wizard.version, product_name="상품"),
        )
        wizard = self.service.update_media(
            wizard.id, self.company_id,
            WizardMediaUpdateRequest(expected_version=wizard.version, selected_media_asset_ids=[media.id]),
        )
        wizard = self.service.update_channels(
            wizard.id, self.company_id,
            WizardChannelsUpdateRequest(expected_version=wizard.version, marketplace_account_ids=[account.id]),
        )
        self._cache_coupang_logistics(wizard.id)
        forged = dict(VALID_REQUIRED_FIELDS)
        forged.update({"companyContactNumber": "FORGED", "returnAddress": "FORGED"})
        wizard = self.service.update_fulfillment(
            wizard.id, self.company_id,
            WizardFulfillmentUpdateRequest(
                expected_version=wizard.version,
                selections=[FulfillmentSelectionInput(
                    marketplace_account_id=account.id,
                    fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                    outbound_shipping_place_code="88001",
                    return_center_code="RET-TEST-1",
                    required_fields=forged,
                )],
            ),
        )
        fields = json.loads(wizard.channel_selections_json)[0]["required_fields"]
        self.assertEqual(fields["companyContactNumber"], "02-0000-0000")
        self.assertEqual(fields["returnAddress"], "테스트 주소")

    # ---------------- 승인 게이트 ----------------

    def test_approve_rejects_fingerprint_mismatch(self):

        candidate, channel, account, media = self._full_setup()
        wizard = self._advance_to_ready_for_approval(candidate, account, media)
        preview = self.service.approval_preview(wizard.id, self.company_id)

        with self.assertRaises(ConflictException):
            self.service.approve(
                wizard.id, self.company_id, approved_by=99,
                recent_auth_token=self._recent_auth_token(99),
                data=WizardApproveRequest(
                    expected_version=preview.version,
                    approval_nonce=preview.approval_nonce,
                    expected_fingerprint="0" * 64,
                    product_image_match_confirmed=True,
                ),
            )

    def test_approve_rejects_reused_nonce(self):

        candidate, channel, account, media = self._full_setup()
        wizard = self._advance_to_ready_for_approval(candidate, account, media)
        preview = self.service.approval_preview(wizard.id, self.company_id)

        approve_data = WizardApproveRequest(
            expected_version=preview.version,
            approval_nonce=preview.approval_nonce,
            expected_fingerprint=preview.fingerprint,
            product_image_match_confirmed=True,
        )
        self.service.approve(
            wizard.id, self.company_id, approved_by=99,
            recent_auth_token=self._recent_auth_token(99), data=approve_data,
        )

        # 두 번째 호출은 recent-auth는 새로 통과하지만(각 호출마다
        # 새 토큰 발급), nonce가 이미 소비됐으므로 그 이유로 실패해야
        # 한다 — recent-auth 누락이 아니라 nonce 재사용 자체를 검증.
        with self.assertRaises(UnauthorizedException):
            self.service.approve(
                wizard.id, self.company_id, approved_by=99,
                recent_auth_token=self._recent_auth_token(99),
                data=approve_data,
            )

    # ---------------- 부분 성공 + 실패 채널만 재시도 ----------------

    def test_partial_failure_then_retry_failed_channels_only(self):

        candidate = self._candidate()
        channel = self._channel()
        account1 = self._account(channel.id, code="acct-a")
        account2 = self._account(channel.id, code="acct-b")
        self._capability(channel.id)
        media = self._media_asset(owner_id=candidate.id)

        wizard = self._create_wizard()
        wizard = self.service.update_source(
            wizard.id, self.company_id,
            WizardSourceUpdateRequest(
                expected_version=wizard.version,
                product_candidate_id=candidate.id,
            ),
        )
        wizard = self.service.update_draft(
            wizard.id, self.company_id,
            WizardDraftUpdateRequest(
                expected_version=wizard.version, product_name="상품",
            ),
        )
        wizard = self.service.update_media(
            wizard.id, self.company_id,
            WizardMediaUpdateRequest(
                expected_version=wizard.version,
                selected_media_asset_ids=[media.id],
            ),
        )
        wizard = self.service.update_channels(
            wizard.id, self.company_id,
            WizardChannelsUpdateRequest(
                expected_version=wizard.version,
                marketplace_account_ids=[account1.id, account2.id],
            ),
        )
        self._cache_coupang_logistics(wizard.id)
        wizard = self.service.update_fulfillment(
            wizard.id, self.company_id,
            WizardFulfillmentUpdateRequest(
                expected_version=wizard.version,
                selections=[
                    FulfillmentSelectionInput(
                        marketplace_account_id=account1.id,
                        fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                        outbound_shipping_place_code="88001",
                        return_center_code="RET-TEST-1",
                        required_fields=VALID_REQUIRED_FIELDS,
                        channel_policy_attributes=VALID_CHANNEL_POLICY_ATTRIBUTES,
                    ),
                    FulfillmentSelectionInput(
                        marketplace_account_id=account2.id,
                        fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                        outbound_shipping_place_code="88001",
                        return_center_code="RET-TEST-1",
                        required_fields=VALID_REQUIRED_FIELDS,
                        channel_policy_attributes=VALID_CHANNEL_POLICY_ATTRIBUTES,
                    ),
                ],
            ),
        )
        wizard = self.service.update_economics(
            wizard.id, self.company_id,
            WizardEconomicsUpdateRequest(
                expected_version=wizard.version,
                items=[
                    EconomicsInputItem(
                        marketplace_account_id=account1.id,
                        cost_of_goods="5000", sale_price="9000",
                    ),
                    EconomicsInputItem(
                        marketplace_account_id=account2.id,
                        cost_of_goods="5000", sale_price="9000",
                    ),
                ],
            ),
        )
        result = self.service.validate(
            wizard.id, self.company_id, wizard.version,
        )
        self.assertEqual(result.status, "READY_FOR_APPROVAL", result.issues)
        wizard = self.service.get(wizard.id, self.company_id)

        preview = self.service.approval_preview(wizard.id, self.company_id)
        approved = self.service.approve(
            wizard.id, self.company_id, approved_by=99,
            recent_auth_token=self._recent_auth_token(99),
            data=WizardApproveRequest(
                expected_version=preview.version,
                approval_nonce=preview.approval_nonce,
                expected_fingerprint=preview.fingerprint,
                product_image_match_confirmed=True,
            ),
        )

        # account2 채널만 실패하도록 원본 create_listing()을 감싼다 —
        # 나머지(create_listing 내부 로직 자체)는 전혀 건드리지 않는다.
        from app.core.exceptions import BadRequestException
        from app.domains.marketplace_listing.service import (
            MarketplaceListingService,
        )
        original_create_listing = MarketplaceListingService.create_listing

        def _flaky_create_listing(self_svc, data, company_id):
            if data.marketplace_account_id == account2.id:
                raise BadRequestException("시뮬레이션된 채널 실패")
            return original_create_listing(self_svc, data, company_id)

        with mock.patch.object(
            MarketplaceListingService, "create_listing",
            _flaky_create_listing,
        ):
            submitted = self.service.submit(
                wizard.id, self.company_id, approved_by=99,
                data=WizardSubmitRequest(
                    expected_version=approved.version,
                    execution_mode="SUBMIT",
                ),
            )

        self.assertEqual(submitted.status, WizardStatus.PARTIALLY_SUCCEEDED)

        results = self.service.results(wizard.id, self.company_id)
        by_account = {c.marketplace_account_id: c for c in results.channels}
        self.assertEqual(by_account[account1.id].status, "PENDING")
        self.assertEqual(by_account[account2.id].status, "FAILED")
        self.assertIsNotNone(by_account[account2.id].error_reason)

        # 패치를 해제한 뒤(더 이상 실패시키지 않음) 실패한 채널만
        # 재시도한다 — account1은 다시 호출되지 않아야 한다(이미
        # idempotency_key로 성공한 행을 그대로 반환하므로 재호출되어도
        # 안전하지만, 재시도 대상 필터링 자체를 검증한다).
        final = self.service.retry_failed_channels(
            wizard.id, self.company_id, approved_by=99,
        )
        self.assertEqual(final.status, WizardStatus.SUCCEEDED)

        final_results = self.service.results(wizard.id, self.company_id)
        for channel_result in final_results.channels:
            self.assertEqual(channel_result.status, "PENDING")

    def test_mode_blocked_submission_is_re_evaluated_on_retry(self):

        candidate, _channel, account, media = self._full_setup()
        wizard = self._advance_to_ready_for_approval(candidate, account, media)
        preview = self.service.approval_preview(wizard.id, self.company_id)
        approved = self.service.approve(
            wizard.id, self.company_id, approved_by=99,
            recent_auth_token=self._recent_auth_token(99),
            data=WizardApproveRequest(
                expected_version=preview.version,
                approval_nonce=preview.approval_nonce,
                expected_fingerprint=preview.fingerprint,
                product_image_match_confirmed=True,
            ),
        )

        SafetyService(self.db).set_mode(
            AutomationMode.RECOMMEND_ONLY, set_by=1, is_admin=True,
        )
        blocked = self.service.submit(
            wizard.id, self.company_id, approved_by=99,
            data=WizardSubmitRequest(
                expected_version=approved.version,
                execution_mode="SUBMIT",
            ),
        )
        self.assertEqual(blocked.status, WizardStatus.FAILED)
        first = self.db.query(MarketplaceSubmission).one()
        self.assertEqual(first.error_reason, "MODE_NOT_ALLOWED")

        # 라이브에서 재현된 핵심 조건: 최초 실행 뒤 시간이 지나 내부
        # 채널 승인이 만료됐더라도, 변경되지 않은 위저드 승인 지문을
        # 바탕으로 재시도용 승인을 새 키로 발급해야 한다.
        original_approved = (
            self.db.query(MarketplaceSubmissionApproval)
            .filter(
                MarketplaceSubmissionApproval.listing_id == first.listing_id,
                MarketplaceSubmissionApproval.status == "APPROVED",
            )
            .one()
        )
        original_approved.expires_at = datetime(2000, 1, 1)
        self.db.commit()

        SafetyService(self.db).set_mode(
            AutomationMode.OPERATOR_APPROVAL, set_by=1, is_admin=True,
        )
        retried = self.service.retry_failed_channels(
            wizard.id, self.company_id, approved_by=99,
        )
        self.assertEqual(retried.status, WizardStatus.SUCCEEDED)

        submissions = (
            self.db.query(MarketplaceSubmission)
            .order_by(MarketplaceSubmission.id)
            .all()
        )
        self.assertEqual(len(submissions), 2)
        self.assertEqual(submissions[0].status, "FAILED")
        self.assertEqual(submissions[1].status, "PENDING")
        self.assertNotEqual(
            submissions[0].idempotency_key,
            submissions[1].idempotency_key,
        )
        self.assertIn("-retry-v", submissions[1].idempotency_key)
        approvals = (
            self.db.query(MarketplaceSubmissionApproval)
            .filter(MarketplaceSubmissionApproval.listing_id == first.listing_id)
            .order_by(MarketplaceSubmissionApproval.id)
            .all()
        )
        self.assertEqual(len(approvals), 4)
        self.assertIn("-retry-v", approvals[-2].idempotency_key)
        self.assertIn("-retry-v", approvals[-1].idempotency_key)

    # ---------------- 복제 ----------------

    def test_clone_does_not_inherit_approval_state(self):

        candidate, channel, account, media = self._full_setup()
        wizard = self._advance_to_ready_for_approval(candidate, account, media)
        preview = self.service.approval_preview(wizard.id, self.company_id)
        approved = self.service.approve(
            wizard.id, self.company_id, approved_by=99,
            recent_auth_token=self._recent_auth_token(99),
            data=WizardApproveRequest(
                expected_version=preview.version,
                approval_nonce=preview.approval_nonce,
                expected_fingerprint=preview.fingerprint,
                product_image_match_confirmed=True,
            ),
        )

        clone, is_dup = self.service.clone(
            approved.id, self.company_id, created_by=1,
            data=WizardCloneRequest(creation_idempotency_key=_key("clone")),
        )

        self.assertFalse(is_dup)
        self.assertEqual(clone.status, WizardStatus.DRAFT)
        self.assertIsNone(clone.approval_fingerprint)
        self.assertIsNone(clone.approved_by_user_id)
        self.assertEqual(
            json.loads(clone.materialized_listing_ids_json), [],
        )
        self.assertEqual(clone.cloned_from_wizard_id, approved.id)
        self.assertEqual(clone.draft_json, approved.draft_json)

    # ---------------- idempotent 생성 ----------------

    def test_create_is_idempotent_by_key(self):

        key = _key("dup")
        first, dup1 = self.service.create(
            WizardCreateRequest(creation_idempotency_key=key),
            created_by=1, company_id=self.company_id,
        )
        second, dup2 = self.service.create(
            WizardCreateRequest(creation_idempotency_key=key),
            created_by=1, company_id=self.company_id,
        )

        self.assertFalse(dup1)
        self.assertTrue(dup2)
        self.assertEqual(first.id, second.id)

    # ---------------- Gate Q-1: 승인 취소 후 수정 ----------------

    def _approve_wizard(self, candidate, account, media, approved_by=99):

        wizard = self._advance_to_ready_for_approval(candidate, account, media)
        preview = self.service.approval_preview(wizard.id, self.company_id)
        approved = self.service.approve(
            wizard.id, self.company_id, approved_by=approved_by,
            recent_auth_token=self._recent_auth_token(approved_by),
            data=WizardApproveRequest(
                expected_version=preview.version,
                approval_nonce=preview.approval_nonce,
                expected_fingerprint=preview.fingerprint,
                product_image_match_confirmed=True,
            ),
        )
        return approved, preview

    def test_revoke_preview_requires_approved_status(self):

        candidate, channel, account, media = self._full_setup()
        wizard = self._advance_to_ready_for_approval(candidate, account, media)

        with self.assertRaises(ConflictException):
            self.service.revoke_approval_preview(wizard.id, self.company_id)

    def test_revoke_approval_unlocks_editing_and_preserves_snapshot(self):

        candidate, channel, account, media = self._full_setup()
        approved, preview = self._approve_wizard(candidate, account, media)
        self.assertEqual(approved.status, WizardStatus.APPROVED)
        old_fingerprint = approved.approval_fingerprint
        old_package = json.loads(approved.approval_package_json)

        revoke_preview = self.service.revoke_approval_preview(
            approved.id, self.company_id,
        )
        self.assertEqual(revoke_preview.fingerprint, old_fingerprint)
        self.assertEqual(revoke_preview.approval_package, old_package)

        revoked = self.service.revoke_approval(
            approved.id, self.company_id, revoked_by=99,
            recent_auth_token=self._recent_auth_token(99),
            data=WizardRevokeApprovalRequest(
                expected_version=revoke_preview.version,
                reason="가격 오타 수정",
                revoke_nonce=revoke_preview.revoke_nonce,
            ),
        )

        # 승인 상태가 무효화되고 다시 편집 가능한 상태로 돌아간다.
        self.assertEqual(revoked.status, WizardStatus.NEEDS_CORRECTION)
        self.assertIsNone(revoked.approval_fingerprint)
        self.assertIsNone(revoked.approved_by_user_id)
        self.assertIsNone(revoked.approved_at)

        # 기존 Package·fingerprint·사유·취소자는 이력에 스냅샷으로
        # 보존된다 — 덮어써지지 않는다.
        history = json.loads(revoked.approval_history_json)
        revoke_entry = history[-1]
        self.assertEqual(revoke_entry["action"], "APPROVAL_REVOKED")
        self.assertEqual(revoke_entry["by_user_id"], 99)
        self.assertEqual(revoke_entry["reason"], "가격 오타 수정")
        self.assertEqual(revoke_entry["preserved_fingerprint"], old_fingerprint)
        self.assertEqual(
            revoke_entry["preserved_approval_package"], old_package,
        )

        # NEEDS_CORRECTION은 EDITABLE 집합에 있으므로 다시 편집할 수
        # 있다 — 승인 전으로 완전히 되돌아간다.
        edited = self.service.update_economics(
            revoked.id, self.company_id,
            data=WizardEconomicsUpdateRequest(
                expected_version=revoked.version,
                items=[EconomicsInputItem(
                    marketplace_account_id=account.id,
                    cost_of_goods="5000", sale_price="20000",
                )],
            ),
        )
        self.assertIsNotNone(edited)

    def test_revoke_approval_requires_recent_auth(self):

        candidate, channel, account, media = self._full_setup()
        approved, _preview = self._approve_wizard(candidate, account, media)
        revoke_preview = self.service.revoke_approval_preview(
            approved.id, self.company_id,
        )

        with self.assertRaises(UnauthorizedException):
            self.service.revoke_approval(
                approved.id, self.company_id, revoked_by=99,
                recent_auth_token=None,
                data=WizardRevokeApprovalRequest(
                    expected_version=revoke_preview.version,
                    reason="사유",
                    revoke_nonce=revoke_preview.revoke_nonce,
                ),
            )

    def test_revoke_approval_rejects_reused_nonce(self):

        candidate, channel, account, media = self._full_setup()
        approved, _preview = self._approve_wizard(candidate, account, media)
        revoke_preview = self.service.revoke_approval_preview(
            approved.id, self.company_id,
        )
        revoke_data = WizardRevokeApprovalRequest(
            expected_version=revoke_preview.version,
            reason="사유",
            revoke_nonce=revoke_preview.revoke_nonce,
        )
        self.service.revoke_approval(
            approved.id, self.company_id, revoked_by=99,
            recent_auth_token=self._recent_auth_token(99),
            data=revoke_data,
        )

        # 같은 nonce로 다시 시도 — 이미 소비됨. recent-auth는 각
        # 호출마다 새로 발급하므로(재사용 자체가 별도로 막혀 있다) 여기서는
        # nonce 재사용만 독립적으로 검증한다.
        with self.assertRaises(UnauthorizedException):
            self.service.revoke_approval(
                approved.id, self.company_id, revoked_by=99,
                recent_auth_token=self._recent_auth_token(99),
                data=revoke_data,
            )

    def test_revoke_approval_blocked_once_submitting_started(self):
        """
        SUBMITTING 이후로는 승인 취소가 절대 도달할 수 없다 —
        `revoke_approval_conditional()`의 `WHERE status == 'APPROVED'`가
        구조적으로 차단한다.
        """

        candidate, channel, account, media = self._full_setup()
        approved, _preview = self._approve_wizard(candidate, account, media)

        submitted = self.service.submit(
            approved.id, self.company_id, approved_by=99,
            data=WizardSubmitRequest(
                expected_version=approved.version, execution_mode="SUBMIT",
            ),
        )
        self.assertEqual(submitted.status, WizardStatus.SUCCEEDED)

        with self.assertRaises(ConflictException):
            self.service.revoke_approval_preview(
                submitted.id, self.company_id,
            )

    def test_revoke_approval_concurrent_exactly_one_succeeds(self):
        """동시에 두 번 취소를 시도해도 정확히 하나만 성공해야 한다
        (조건부 UPDATE의 WHERE status=='APPROVED' + version 일치)."""

        candidate, channel, account, media = self._full_setup()
        approved, _preview = self._approve_wizard(candidate, account, media)
        revoke_preview = self.service.revoke_approval_preview(
            approved.id, self.company_id,
        )
        stale_version = revoke_preview.version

        self.service.revoke_approval(
            approved.id, self.company_id, revoked_by=99,
            recent_auth_token=self._recent_auth_token(99),
            data=WizardRevokeApprovalRequest(
                expected_version=stale_version,
                reason="첫 번째 취소",
                revoke_nonce=revoke_preview.revoke_nonce,
            ),
        )

        # 두 번째 시도는 이미 소비된 nonce이자 이미 바뀐 version이라
        # 어느 쪽으로도 실패해야 한다 — nonce부터 걸린다.
        with self.assertRaises(UnauthorizedException):
            self.service.revoke_approval(
                approved.id, self.company_id, revoked_by=99,
                recent_auth_token=self._recent_auth_token(99),
                data=WizardRevokeApprovalRequest(
                    expected_version=stale_version,
                    reason="두 번째 취소(경쟁)",
                    revoke_nonce=revoke_preview.revoke_nonce,
                ),
            )

    def test_revoke_then_edit_then_reapprove_generates_new_fingerprint(self):

        candidate, channel, account, media = self._full_setup()
        approved, _preview = self._approve_wizard(candidate, account, media)
        old_fingerprint = approved.approval_fingerprint

        revoke_preview = self.service.revoke_approval_preview(
            approved.id, self.company_id,
        )
        revoked = self.service.revoke_approval(
            approved.id, self.company_id, revoked_by=99,
            recent_auth_token=self._recent_auth_token(99),
            data=WizardRevokeApprovalRequest(
                expected_version=revoke_preview.version,
                reason="가격 변경",
                revoke_nonce=revoke_preview.revoke_nonce,
            ),
        )

        edited = self.service.update_economics(
            revoked.id, self.company_id,
            data=WizardEconomicsUpdateRequest(
                expected_version=revoked.version,
                items=[EconomicsInputItem(
                    marketplace_account_id=account.id,
                    cost_of_goods="5000", sale_price="30000",
                )],
            ),
        )

        revalidated = self.service.validate(
            edited.id, self.company_id, edited.version,
        )
        self.assertEqual(revalidated.status, "READY_FOR_APPROVAL")

        new_preview = self.service.approval_preview(
            edited.id, self.company_id,
        )
        self.assertNotEqual(new_preview.fingerprint, old_fingerprint)

        reapproved = self.service.approve(
            edited.id, self.company_id, approved_by=99,
            recent_auth_token=self._recent_auth_token(99),
            data=WizardApproveRequest(
                expected_version=new_preview.version,
                approval_nonce=new_preview.approval_nonce,
                expected_fingerprint=new_preview.fingerprint,
                product_image_match_confirmed=True,
            ),
        )
        self.assertEqual(reapproved.status, WizardStatus.APPROVED)
        self.assertEqual(reapproved.approval_fingerprint, new_preview.fingerprint)


if __name__ == "__main__":
    unittest.main()
