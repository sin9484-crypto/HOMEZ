"""
=========================================================
Homez OS

File : tests/test_listing_wizard_permission.py

Gate Q-2(2026-08-09) — 상품등록 마법사 세부 Permission 기반 VIEWER
정책 검증. 전부 임시 SQLite DB에서만 검증한다(실제 homez.db에는
Permission 행을 만들지 않는다 — listing_wizard_permissions.py 상단
주석 참고).

두 층을 나눠서 검증한다:
1) `ListingWizardPermissionGuardTestCase` — Guard 함수 자체를 FastAPI
   Depends 해석 없이 직접 호출해, ADMIN/SUPER_ADMIN은 Permission 행과
   무관하게 항상 통과하고, 그 외 역할은 실제로 RolePermission이
   부여된 코드에서만 통과하는지 확인한다.
2) `ListingWizardPermissionResponseShapeTestCase` — 실제 서비스로
   위저드를 만들고, 라우터 함수(`_to_detail` 경유)가
   LISTING_ECONOMICS_VIEW 권한 여부에 따라 금액 필드를 가리는지
   확인한다(라우터 함수 직접 호출은 Depends 체인을 타지 않으므로
   Guard 자체는 여기서 검증하지 않는다 — 1)에서 이미 검증됨).
=========================================================
"""

import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.authorization import UserRole
from app.core.guard import SuperAdminGuard
from app.core.recent_auth import issue_recent_auth_token
from app.core.security import hash_password
from app.database.base import Base
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.automation_safety.service import SafetyService
from app.domains.company.model import Company
from app.domains.marketplace_listing import listing_wizard_router as wr
from app.domains.marketplace_listing.constants import CapabilityStatus
from app.domains.marketplace_listing.constants import FulfillmentMode
from app.domains.marketplace_listing.category_metadata import (
    notice_input_fingerprint,
)
from app.domains.marketplace_listing.coupang_logistics_provider import (
    LogisticsLocation,
    cache_locations,
)
from app.domains.marketplace_listing.listing_wizard_approval_nonce import (
    clear_all_wizard_approval_nonces,
)
from app.domains.marketplace_listing.listing_wizard_permission_guard import (
    ListingWizardPermissionGuard,
)
from app.domains.marketplace_listing.listing_wizard_permission_guard import (
    user_can,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_ECONOMICS_VIEW,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_APPROVE,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_CREATE,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_EDIT,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_RETRY,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_SUBMIT,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_VIEW,
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
    WizardCreateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardDraftUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardEconomicsUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    EconomicsInputItem,
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
from app.domains.marketplace_listing.listing_wizard_service import (
    ListingWizardService,
)
from app.domains.marketplace_listing.model import ListingWizard
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
from app.domains.channel_policy.model import ChannelPolicyEvaluation
from app.domains.channel_policy.model import ChannelPolicyRule
from app.domains.channel_policy.model import CompanyChannelPolicySettings
from app.domains.media_asset.model import MediaAsset
from app.domains.permission.model import Permission
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.role.model import Role
from app.domains.role_permission.model import RolePermission
from app.domains.user.model import User

_COUNTER = 0


def _key(prefix: str) -> str:

    global _COUNTER
    _COUNTER += 1
    return f"{prefix}-{_COUNTER}"


class _ListingWizardPermissionTestBase(unittest.TestCase):

    def setUp(self):

        clear_all_wizard_approval_nonces()

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, Role.__table__, Permission.__table__,
                RolePermission.__table__, User.__table__,
                ProductCandidate.__table__,
                ProductCandidateSelection.__table__,
                MarketplaceChannel.__table__, MarketplaceAccount.__table__,
                MarketplaceFulfillmentCapability.__table__,
                MarketplaceListingDraft.__table__, MarketplaceListing.__table__,
                MarketplaceFulfillmentSelection.__table__,
                MarketplaceSubmissionApproval.__table__,
                MarketplaceFulfillmentEligibility.__table__,
                MarketplaceSubmission.__table__, ListingWizard.__table__,
                MediaAsset.__table__, AutomationModeState.__table__,
                EmergencyStop.__table__, ExecutionLimit.__table__,
                ExecutionUsage.__table__, ExecutionPeriodUsage.__table__,
                ChannelPolicyRule.__table__,
                CompanyChannelPolicySettings.__table__,
                ChannelPolicyEvaluation.__table__,
            ],
        )
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        SafetyService(self.db).set_mode(
            AutomationMode.OPERATOR_APPROVAL, set_by=1, is_admin=True,
        )

        self.company = Company(
            name="A", business_number="1", ceo="t", phone="t",
            email="a@example.com", address="t",
        )
        self.db.add(self.company)
        self.db.commit()

        self.admin_role = Role(name="Administrator", code="ADMIN")
        self.viewer_role = Role(name="Viewer", code="VIEWER")
        self.db.add_all([self.admin_role, self.viewer_role])
        self.db.commit()

        # Gate Q-2 8개 코드를 "임시 DB 전용"으로만 만든다 — 실제
        # homez.db에는 만들지 않는다(파일 상단 docstring 참고).
        self.permissions = {}
        for code in (
            LISTING_WIZARD_VIEW, LISTING_WIZARD_CREATE, LISTING_WIZARD_EDIT,
            LISTING_WIZARD_APPROVE, LISTING_WIZARD_SUBMIT,
            LISTING_WIZARD_RETRY, LISTING_ECONOMICS_VIEW,
        ):
            perm = Permission(name=code, code=code, active=True)
            self.db.add(perm)
            self.permissions[code] = perm
        self.db.commit()

        self.admin_user = User(
            username="admin1", email="admin1@example.com",
            password_hash=hash_password("Str0ng!Passw0rd"),
            role_id=self.admin_role.id, company_id=self.company.id,
            is_active=True,
        )
        self.viewer_user = User(
            username="viewer1", email="viewer1@example.com",
            password_hash=hash_password("Str0ng!Passw0rd"),
            role_id=self.viewer_role.id, company_id=self.company.id,
            is_active=True,
        )
        self.db.add_all([self.admin_user, self.viewer_user])
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        clear_all_wizard_approval_nonces()

    def _grant(self, role: Role, code: str):

        self.db.add(RolePermission(
            role_id=role.id, permission_id=self.permissions[code].id,
        ))
        self.db.commit()


class ListingWizardPermissionGuardTestCase(_ListingWizardPermissionTestBase):
    """Guard 함수 자체를 직접 호출한다(FastAPI Depends 해석 없이)."""

    def test_admin_always_passes_regardless_of_permission_rows(self):

        for code in (
            LISTING_WIZARD_VIEW, LISTING_WIZARD_CREATE, LISTING_WIZARD_EDIT,
            LISTING_WIZARD_SUBMIT, LISTING_WIZARD_RETRY,
        ):
            guard = ListingWizardPermissionGuard(code)
            result = guard(current_user=self.admin_user, db=self.db)
            self.assertEqual(result.id, self.admin_user.id, code)

    def test_viewer_without_any_grant_is_denied_everywhere(self):

        for code in (
            LISTING_WIZARD_VIEW, LISTING_WIZARD_CREATE, LISTING_WIZARD_EDIT,
            LISTING_WIZARD_SUBMIT, LISTING_WIZARD_RETRY,
        ):
            guard = ListingWizardPermissionGuard(code)
            with self.assertRaises(HTTPException) as ctx:
                guard(current_user=self.viewer_user, db=self.db)
            self.assertEqual(ctx.exception.status_code, 403, code)

    def test_viewer_view_only_grant_does_not_unlock_mutating_actions(self):
        """VIEWER 읽기 전용 — 조회는 되지만 생성/편집/제출/재시도는
        여전히 막힌다(코드별 독립 통제 확인)."""

        self._grant(self.viewer_role, LISTING_WIZARD_VIEW)

        result = ListingWizardPermissionGuard(LISTING_WIZARD_VIEW)(
            current_user=self.viewer_user, db=self.db,
        )
        self.assertEqual(result.id, self.viewer_user.id)

        for code in (
            LISTING_WIZARD_CREATE, LISTING_WIZARD_EDIT,
            LISTING_WIZARD_SUBMIT, LISTING_WIZARD_RETRY,
        ):
            with self.assertRaises(HTTPException) as ctx:
                ListingWizardPermissionGuard(code)(
                    current_user=self.viewer_user, db=self.db,
                )
            self.assertEqual(ctx.exception.status_code, 403, code)

    def test_viewer_retry_grant_does_not_unlock_submit(self):

        self._grant(self.viewer_role, LISTING_WIZARD_RETRY)

        result = ListingWizardPermissionGuard(LISTING_WIZARD_RETRY)(
            current_user=self.viewer_user, db=self.db,
        )
        self.assertEqual(result.id, self.viewer_user.id)

        with self.assertRaises(HTTPException) as ctx:
            ListingWizardPermissionGuard(LISTING_WIZARD_SUBMIT)(
                current_user=self.viewer_user, db=self.db,
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_permission_grant_never_substitutes_for_super_admin_guard(self):
        """
        승인(approve)/승인 취소(revoke-approval)는 Permission 모델과
        무관하게 SuperAdminGuard(역할 기반)로만 통제된다 — VIEWER가
        LISTING_WIZARD_APPROVE를 부여받아도 실제 승인/취소 엔드포인트
        자체는 여전히 SUPER_ADMIN 전용이다(다층 방어).
        """

        self._grant(self.viewer_role, LISTING_WIZARD_APPROVE)

        # approval-preview(GET)는 Permission 기반으로 통과할 수 있다.
        result = ListingWizardPermissionGuard(LISTING_WIZARD_APPROVE)(
            current_user=self.viewer_user, db=self.db,
        )
        self.assertEqual(result.id, self.viewer_user.id)

        # 그러나 실제 승인/취소 실행은 SuperAdminGuard가 별도로 막는다.
        with self.assertRaises(HTTPException) as ctx:
            SuperAdminGuard(current_user=self.viewer_user)
        self.assertEqual(ctx.exception.status_code, 403)


class ListingWizardEconomicsVisibilityTestCase(_ListingWizardPermissionTestBase):
    """
    Gate Q-2 — LISTING_ECONOMICS_VIEW은 LISTING_WIZARD_VIEW와 독립
    통제된다. 실제 서비스로 금액 데이터를 채운 위저드를 만든 뒤,
    권한별로 라우터 응답(`_to_detail`)에서 금액 필드가 가려지는지
    `user_can()` 헬퍼를 통해 직접 확인한다.
    """

    def _create_wizard_with_economics(self):

        service = ListingWizardService(self.db)
        wizard, _dup = service.create(
            WizardCreateRequest(creation_idempotency_key=_key("k")),
            created_by=self.admin_user.id, company_id=self.company.id,
        )
        channel = MarketplaceChannel(
            code="COUPANG", name="쿠팡", doc_verification_status="VERIFIED",
        )
        self.db.add(channel)
        self.db.commit()
        account = MarketplaceAccount(
            company_id=self.company.id, channel_id=channel.id,
            account_code="acct-1", account_name="테스트계정",
        )
        self.db.add(account)
        self.db.commit()

        updated = service.update_economics(
            wizard.id, self.company.id,
            data=WizardEconomicsUpdateRequest(
                expected_version=wizard.version,
                items=[EconomicsInputItem(
                    marketplace_account_id=account.id,
                    cost_of_goods="5000", sale_price="20000",
                )],
            ),
        )
        return updated

    def test_view_only_permission_hides_economics_fields(self):
        """
        Gate U-1(2026-08-10) — 값을 빈 배열로 가리는 게 아니라 키 자체를
        응답에서 제외해야 한다(강화된 계약). `_to_detail()`이 이제
        Pydantic 모델이 아닌 dict를 반환하므로 속성 접근이 아니라 키
        존재 여부로 확인한다.
        """

        wizard = self._create_wizard_with_economics()
        self.assertNotEqual(wizard.economics_input_json, "[]")

        detail = wr.get_wizard(
            wizard.id, current_user=self.viewer_user, db=self.db,
        )
        # 라우터 함수 직접 호출은 Depends 체인을 타지 않으므로 여기서는
        # Guard 통과 여부가 아니라 `_to_detail`의 가시성 로직만 본다
        # (Guard 자체는 ListingWizardPermissionGuardTestCase가 전담).
        self.assertNotIn("economics_input", detail)
        self.assertNotIn("economics_result", detail)

    def test_economics_view_grant_reveals_real_amounts(self):

        wizard = self._create_wizard_with_economics()
        self._grant(self.viewer_role, LISTING_ECONOMICS_VIEW)

        detail = wr.get_wizard(
            wizard.id, current_user=self.viewer_user, db=self.db,
        )
        expected = json.loads(wizard.economics_input_json)[0][
            "marketplace_account_id"
        ]
        self.assertIn("economics_input", detail)
        self.assertNotEqual(detail["economics_input"], [])
        self.assertEqual(
            detail["economics_input"][0]["marketplace_account_id"], expected,
        )

    def test_admin_always_sees_economics_without_any_grant(self):

        wizard = self._create_wizard_with_economics()

        detail = wr.get_wizard(
            wizard.id, current_user=self.admin_user, db=self.db,
        )
        self.assertIn("economics_input", detail)
        self.assertNotEqual(detail["economics_input"], [])

    def test_user_can_helper_matches_guard_semantics(self):

        self.assertTrue(
            user_can(self.db, self.admin_user, LISTING_ECONOMICS_VIEW),
        )
        self.assertFalse(
            user_can(self.db, self.viewer_user, LISTING_ECONOMICS_VIEW),
        )
        self._grant(self.viewer_role, LISTING_ECONOMICS_VIEW)
        self.assertTrue(
            user_can(self.db, self.viewer_user, LISTING_ECONOMICS_VIEW),
        )


# 2026-08-30 V7 안정화 Phase 1(감사 F-01) — 단일 제출 계약 통합
# 이후 7단계 사전검사도 실제 Live 제출과 동일한 최소 유효 값을
# 요구한다(tests/test_listing_wizard_service.py의 VALID_REQUIRED_
# FIELDS와 동일한 이유로 동일하게 보강 — 그 파일의 주석 참고).
_VALID_IMAGES = [{
    "imageOrder": 0, "imageType": "REPRESENTATION",
    "vendorPath": "https://images.example.test/product.png",
}]
_VALID_NOTICES = [{
    "noticeCategoryName": "기타 재화",
    "noticeCategoryDetailNames": [{
        "noticeCategoryDetailName": "품명 및 모델명",
        "content": "테스트 상품",
    }],
}]
_VALID_CONTENTS = [{
    "contentsType": "TEXT",
    "contentDetails": [{"content": "테스트 상품 상세 설명", "detailType": "TEXT"}],
}]
_VALID_REQUIRED_FIELDS = {
    "deliveryMethod": "AGENT_BUY",
    "deliveryChargeType": "FREE",
    "deliveryCharge": "0",
    "deliveryChargeOnReturn": "2500",
    "returnCharge": "2500",
    "returnCenterCode": "RC001",
    "outboundShippingPlaceCode": "88002",
    "returnChargeName": "반품지",
    "companyContactNumber": "0212345678",
    "returnZipCode": "12345",
    "returnAddress": "서울시 강남구",
    "items": [{"itemName": "기본형", "externalVendorSku": "SKU-001"}],
    "originalPrice": "10000",
    "salePrice": "9000",
    "maximumBuyCount": 100,
    "displayCategoryCode": 99999,
    "images": _VALID_IMAGES,
    "notices": _VALID_NOTICES,
    "contents": _VALID_CONTENTS,
    "liveImageRightsConfirmed": True,
    "vendorUserId": "sin945",
    "deliveryCompanyCode": "CJGLS",
    "brandState": "NO_BRAND",
    "brand": "HOMEZ",
}
_VALID_CHANNEL_POLICY_ATTRIBUTES = {
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


class ListingWizardApprovalPreviewRedactionTestCase(
    _ListingWizardPermissionTestBase,
):
    """
    Gate U-1(2026-08-10) — `approval_preview`/`revoke_approval_preview`는
    지금까지 LISTING_WIZARD_APPROVE만으로 통과됐고, 반환하는
    `approval_package`에는 항상 실제 economics_input/economics_result가
    그대로 담겨 있었다(fingerprint 계산에 필요하므로) — Economics 열람
    권한이 없는 승인권자에게도 그대로 노출되는 게 실제 결함이었다.
    이 클래스는 두 엔드포인트 모두 `LISTING_ECONOMICS_VIEW`가 없으면
    `approval_package`에서 두 키를 제거하고, 있으면 그대로 보여주는지
    확인한다.
    """

    def setUp(self):

        super().setUp()

        # 실제 저장소에는 pending Migration 파일이 존재해 이 프로세스
        # 기준으로 재계산하면 항상 제한 모드가 된다(test_listing_wizard_
        # service.py와 동일한 이유로 동일하게 고정) — 이 클래스는 제한
        # 모드 신호 자체가 아니라 Economics redaction만 검증한다.
        self._restricted_mode_patcher = mock.patch(
            "app.domains.marketplace_listing.listing_wizard_precheck."
            "is_restricted_mode",
            return_value=False,
        )
        self._restricted_mode_patcher.start()
        self.addCleanup(self._restricted_mode_patcher.stop)

    def _advance_to_ready_for_approval(self):

        candidate = ProductCandidate(
            candidate_key=f"wiz:COUPANG:{_key('cand')}", source_type="TREND",
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
            company_id=self.company.id, channel_id=channel.id,
            account_code="acct-1", account_name="테스트계정",
        )
        self.db.add(account)
        self.db.commit()

        capability = MarketplaceFulfillmentCapability(
            channel_id=channel.id, fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
            is_supported=True, requires_eligibility_check=False,
            requires_account_contract=False,
            external_display_name=FulfillmentMode.SELLER_FULFILLED,
            policy_version="1.0.0", schema_name="coupang_seller_fulfilled",
            schema_version="1.0.0", doc_source_reference="test",
            status=CapabilityStatus.VERIFIED, verified_at=datetime.utcnow(),
        )
        self.db.add(capability)
        self.db.commit()

        media = MediaAsset(
            company_id=self.company.id, owner_type="PRODUCT_CANDIDATE",
            owner_id=candidate.id, asset_role="GENERATED", purpose="MAIN",
            display_order=0, storage_path=f"media/{_key('path')}.png",
            mime_type="image/png", file_size_bytes=1024, sha256_hex="a" * 64,
            rights_status="VERIFIED",
        )
        self.db.add(media)
        self.db.commit()

        service = ListingWizardService(self.db)
        wizard, _dup = service.create(
            WizardCreateRequest(creation_idempotency_key=_key("k")),
            created_by=self.admin_user.id, company_id=self.company.id,
        )
        wizard = service.update_source(
            wizard.id, self.company.id,
            WizardSourceUpdateRequest(
                expected_version=wizard.version,
                product_candidate_id=candidate.id,
            ),
        )
        wizard = service.update_draft(
            wizard.id, self.company.id,
            WizardDraftUpdateRequest(
                expected_version=wizard.version, product_name="테스트 상품",
                brand="HOMEZ", category="생활용품",
            ),
        )
        wizard = service.update_media(
            wizard.id, self.company.id,
            WizardMediaUpdateRequest(
                expected_version=wizard.version,
                selected_media_asset_ids=[media.id],
            ),
        )
        wizard = service.update_channels(
            wizard.id, self.company.id,
            WizardChannelsUpdateRequest(
                expected_version=wizard.version,
                marketplace_account_ids=[account.id],
            ),
        )
        cache_locations(self.company.id, wizard.id, "outbound", [
            LogisticsLocation(code="88002", name="출고지", usable=True),
        ])
        cache_locations(self.company.id, wizard.id, "return", [
            LogisticsLocation(
                code="RET-PERM-1", name="반품지", usable=True,
                contact_number="02-0000-0000", zip_code="00000",
                address="테스트 주소", address_detail="상세 주소",
            ),
        ])
        wizard = service.update_fulfillment(
            wizard.id, self.company.id,
            WizardFulfillmentUpdateRequest(
                expected_version=wizard.version,
                selections=[FulfillmentSelectionInput(
                    marketplace_account_id=account.id,
                    fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                    outbound_shipping_place_code="88002",
                    return_center_code="RET-PERM-1",
                    required_fields=_VALID_REQUIRED_FIELDS,
                    channel_policy_attributes=_VALID_CHANNEL_POLICY_ATTRIBUTES,
                )],
            ),
        )
        wizard = service.update_economics(
            wizard.id, self.company.id,
            WizardEconomicsUpdateRequest(
                expected_version=wizard.version,
                items=[EconomicsInputItem(
                    marketplace_account_id=account.id,
                    cost_of_goods="5000", sale_price="9000",
                    channel_fee_rate="0.1",
                )],
            ),
        )
        result = service.validate(wizard.id, self.company.id, wizard.version)
        self.assertEqual(result.status, "READY_FOR_APPROVAL", result.issues)

        return service.get(wizard.id, self.company.id)

    def test_approval_preview_hides_economics_without_grant(self):

        self._grant(self.viewer_role, LISTING_WIZARD_APPROVE)
        wizard = self._advance_to_ready_for_approval()

        preview = wr.approval_preview(
            wizard.id, current_user=self.viewer_user, db=self.db,
        )

        self.assertNotIn("economics_input", preview.approval_package)
        self.assertNotIn("economics_result", preview.approval_package)
        # fingerprint 자체는 여전히 전체 내용(economics 포함) 기준으로
        # 계산된 값 그대로다 — 내부 계약은 이 redaction과 무관하다.
        self.assertTrue(preview.fingerprint)

    def test_approval_preview_reveals_economics_with_grant(self):

        self._grant(self.viewer_role, LISTING_WIZARD_APPROVE)
        self._grant(self.viewer_role, LISTING_ECONOMICS_VIEW)
        wizard = self._advance_to_ready_for_approval()

        preview = wr.approval_preview(
            wizard.id, current_user=self.viewer_user, db=self.db,
        )

        self.assertIn("economics_input", preview.approval_package)
        self.assertNotEqual(preview.approval_package["economics_input"], [])

    def test_revoke_approval_preview_hides_economics_without_grant(self):

        self._grant(self.viewer_role, LISTING_WIZARD_APPROVE)
        wizard = self._advance_to_ready_for_approval()

        service = ListingWizardService(self.db)
        approval_preview = service.approval_preview(wizard.id, self.company.id)
        approved = service.approve(
            wizard.id, self.company.id, approved_by=self.admin_user.id,
            recent_auth_token=issue_recent_auth_token(self.admin_user.id)[0],
            data=WizardApproveRequest(
                expected_version=approval_preview.version,
                approval_nonce=approval_preview.approval_nonce,
                expected_fingerprint=approval_preview.fingerprint,
                product_image_match_confirmed=True,
            ),
        )
        self.assertEqual(approved.status, "APPROVED")

        revoke_preview = wr.revoke_approval_preview(
            wizard.id, current_user=self.viewer_user, db=self.db,
        )

        self.assertNotIn("economics_input", revoke_preview.approval_package)
        self.assertNotIn("economics_result", revoke_preview.approval_package)

    def test_revoke_approval_preview_reveals_economics_with_grant(self):

        self._grant(self.viewer_role, LISTING_WIZARD_APPROVE)
        self._grant(self.viewer_role, LISTING_ECONOMICS_VIEW)
        wizard = self._advance_to_ready_for_approval()

        service = ListingWizardService(self.db)
        approval_preview = service.approval_preview(wizard.id, self.company.id)
        service.approve(
            wizard.id, self.company.id, approved_by=self.admin_user.id,
            recent_auth_token=issue_recent_auth_token(self.admin_user.id)[0],
            data=WizardApproveRequest(
                expected_version=approval_preview.version,
                approval_nonce=approval_preview.approval_nonce,
                expected_fingerprint=approval_preview.fingerprint,
                product_image_match_confirmed=True,
            ),
        )

        revoke_preview = wr.revoke_approval_preview(
            wizard.id, current_user=self.viewer_user, db=self.db,
        )

        self.assertIn("economics_input", revoke_preview.approval_package)
        self.assertNotEqual(revoke_preview.approval_package["economics_input"], [])


class ListingWizardFrontendBackendParityTestCase(unittest.TestCase):
    """
    "프론트엔드 메뉴 vs 백엔드 Permission 일치" — 이 화면은 프론트가
    독자적으로 Permission 코드를 알거나 메뉴를 감추지 않는다(단일
    진실 공급원은 항상 서버 403이다). 프론트가 이 8개 코드 문자열을
    하드코딩하지 않는지만 확인해, "프론트만 고쳐서 그림자 권한 로직이
    생기는" 회귀를 방지한다.
    """

    def test_console_js_does_not_hardcode_permission_codes(self):

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(repo_root, "app", "web", "console.js")
        with open(path, encoding="utf-8") as f:
            js = f.read()

        for code in (
            LISTING_WIZARD_VIEW, LISTING_WIZARD_CREATE, LISTING_WIZARD_EDIT,
            LISTING_WIZARD_APPROVE, LISTING_WIZARD_SUBMIT,
            LISTING_WIZARD_RETRY, LISTING_ECONOMICS_VIEW,
        ):
            self.assertNotIn(code, js)


if __name__ == "__main__":
    unittest.main()
