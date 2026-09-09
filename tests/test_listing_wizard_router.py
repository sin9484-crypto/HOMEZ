"""
=========================================================
Homez OS

File : tests/test_listing_wizard_router.py

Gate I(2026-08-08) — 상품등록 통합 마법사 Router 검증. 이 저장소는
httpx가 없어 FastAPI TestClient를 쓰지 않는다(기존 관례 —
tests/test_company_router_security.py 등과 동일) — 대신 (1) 실제
FastAPI Dependant 체인을 직접 읽어 가드 배선을 정적으로 확인하고,
(2) 라우터 함수를 실제 Session·User 객체로 직접 호출해 응답 변환
(_to_detail)까지 포함한 배선 자체를 검증한다. 비즈니스 로직 자체의
전체 시나리오는 tests/test_listing_wizard_service.py가 이미 전담
검증한다 — 여기서는 그 로직을 라우터가 올바르게 노출하는지만 본다.
=========================================================
"""

import os
import tempfile
import types
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.core.guard import SuperAdminGuard
from app.core.guard import admin_guard
from app.core.recent_auth import issue_recent_auth_token
from app.core.recent_auth import reset_recent_auth_state_for_tests
from app.core.windows_credential_store import InMemoryCredentialStore
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
from app.domains.marketplace_listing.listing_wizard_approval_nonce import (
    clear_all_wizard_approval_nonces,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    CoupangContentsFromMediaRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkArchiveRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardCreateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardDeleteRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardDraftUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardMediaUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardRestoreRequest,
)
from app.domains.marketplace_listing.model import ListingWizard
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


class ListingWizardRouterWiringTestCase(unittest.TestCase):
    """실제 FastAPI Dependant 체인을 읽어 가드 배선을 확인한다."""

    def _route(self, path: str, method: str):

        return next(
            r for r in wr.router.routes
            if r.path == path and method in r.methods
        )

    def test_approve_requires_super_admin_guard(self):

        route = self._route("/listing-wizards/{wizard_id}/approve", "POST")
        calls = [d.call for d in route.dependant.dependencies]

        self.assertIn(SuperAdminGuard, calls)
        self.assertNotIn(admin_guard, calls)

    def test_revoke_approval_requires_super_admin_guard(self):

        route = self._route(
            "/listing-wizards/{wizard_id}/revoke-approval", "POST",
        )
        calls = [d.call for d in route.dependant.dependencies]

        self.assertIn(SuperAdminGuard, calls)
        self.assertNotIn(admin_guard, calls)

    def test_revoke_approval_preview_uses_permission_guard_not_role_guards(self):

        route = self._route(
            "/listing-wizards/{wizard_id}/revoke-approval-preview", "GET",
        )
        calls = [d.call for d in route.dependant.dependencies]

        self.assertNotIn(admin_guard, calls)
        self.assertNotIn(SuperAdminGuard, calls)
        self.assertTrue(any(
            getattr(c, "__qualname__", "").startswith(
                "ListingWizardPermissionGuard.",
            )
            for c in calls
        ))

    def test_other_write_endpoints_use_permission_guard_not_role_guards(self):
        """
        Gate Q-2(2026-08-09) — admin_guard를 세부 Permission 기반
        Guard로 교체했다. 정확히 어떤 Permission 코드가 쓰이는지는
        tests/test_listing_wizard_permission.py의 기능 테스트가
        검증한다 — 여기서는 배선 수준에서 "더 이상 무조건 admin_guard/
        SuperAdminGuard가 아니라 ListingWizardPermissionGuard로
        교체됐는지"만 확인한다.
        """

        for path, method in [
            ("/listing-wizards", "POST"),
            ("/listing-wizards/{wizard_id}", "GET"),
            ("/listing-wizards/{wizard_id}/source", "PATCH"),
            ("/listing-wizards/{wizard_id}/draft", "PATCH"),
            ("/listing-wizards/{wizard_id}/media", "PATCH"),
            ("/listing-wizards/{wizard_id}/channels", "PATCH"),
            ("/listing-wizards/{wizard_id}/fulfillment", "PATCH"),
            ("/listing-wizards/{wizard_id}/economics", "PATCH"),
            ("/listing-wizards/{wizard_id}/validate", "POST"),
            ("/listing-wizards/{wizard_id}/approval-preview", "GET"),
            ("/listing-wizards/{wizard_id}/submit", "POST"),
            ("/listing-wizards/{wizard_id}/retry-failed", "POST"),
            ("/listing-wizards/{wizard_id}/clone", "POST"),
            ("/listing-wizards/{wizard_id}/results", "GET"),
            ("/listing-wizards/{wizard_id}", "DELETE"),
            ("/listing-wizards/{wizard_id}/restore", "POST"),
            ("/listing-wizards/bulk-archive-preview", "GET"),
            ("/listing-wizards/bulk-archive", "POST"),
            ("/listing-wizards/{wizard_id}/coupang/auto-upload-images", "POST"),
            ("/listing-wizards/{wizard_id}/coupang/contents-from-media", "POST"),
        ]:
            route = self._route(path, method)
            calls = [d.call for d in route.dependant.dependencies]
            self.assertNotIn(admin_guard, calls, path)
            self.assertNotIn(SuperAdminGuard, calls, path)
            self.assertTrue(
                any(
                    getattr(c, "__qualname__", "").startswith(
                        "ListingWizardPermissionGuard.",
                    )
                    for c in calls
                ),
                path,
            )

    def test_all_fourteen_plus_endpoints_are_registered(self):

        paths = {r.path for r in wr.router.routes}
        expected = {
            "/listing-wizards", "/listing-wizards/{wizard_id}",
            "/listing-wizards/{wizard_id}/source",
            "/listing-wizards/{wizard_id}/draft",
            "/listing-wizards/{wizard_id}/media",
            "/listing-wizards/{wizard_id}/channels",
            "/listing-wizards/{wizard_id}/fulfillment",
            "/listing-wizards/{wizard_id}/economics",
            "/listing-wizards/{wizard_id}/validate",
            "/listing-wizards/{wizard_id}/approval-preview",
            "/listing-wizards/{wizard_id}/approve",
            "/listing-wizards/{wizard_id}/revoke-approval-preview",
            "/listing-wizards/{wizard_id}/revoke-approval",
            "/listing-wizards/{wizard_id}/submit",
            "/listing-wizards/{wizard_id}/retry-failed",
            "/listing-wizards/{wizard_id}/clone",
            "/listing-wizards/{wizard_id}/results",
        }
        self.assertTrue(expected.issubset(paths))


class ListingWizardRouterFunctionalTestCase(unittest.TestCase):
    """라우터 함수를 실제 Session·User로 직접 호출한다(TestClient 대체)."""

    def setUp(self):

        clear_all_wizard_approval_nonces()

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
            ],
        )
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        SafetyService(self.db).set_mode(
            AutomationMode.OPERATOR_APPROVAL, set_by=1, is_admin=True,
        )

        company_a = Company(
            name="A", business_number="1", ceo="t", phone="t",
            email="a@example.com", address="t",
        )
        company_b = Company(
            name="B", business_number="2", ceo="t", phone="t",
            email="b@example.com", address="t",
        )
        self.db.add_all([company_a, company_b])
        self.db.commit()

        self.user_a = types.SimpleNamespace(
            id=1, company_id=company_a.id, role="ADMIN",
        )
        self.user_b = types.SimpleNamespace(
            id=2, company_id=company_b.id, role="ADMIN",
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        clear_all_wizard_approval_nonces()

    def test_create_and_get_round_trip_via_router_functions(self):
        """
        Gate U-1(2026-08-10) — `_to_detail()`이 이제 Pydantic 모델이
        아닌 dict를 반환하므로(경제성 필드 키 완전 제외를 위해
        response_model을 쓰지 않는다) 라우터 함수 반환값은 속성
        접근이 아니라 키 접근으로 확인한다.
        """

        created = wr.create_wizard(
            WizardCreateRequest(creation_idempotency_key=_key("k")),
            current_user=self.user_a, db=self.db,
        )
        self.assertEqual(created["status"], "DRAFT")
        self.assertEqual(created["current_step"], "SOURCE")

        fetched = wr.get_wizard(
            created["id"], current_user=self.user_a, db=self.db,
        )
        self.assertEqual(fetched["id"], created["id"])

    def test_cross_company_get_via_router_raises_not_found(self):

        created = wr.create_wizard(
            WizardCreateRequest(creation_idempotency_key=_key("k")),
            current_user=self.user_a, db=self.db,
        )

        with self.assertRaises(NotFoundException):
            wr.get_wizard(created["id"], current_user=self.user_b, db=self.db)

    def test_update_draft_via_router_reflects_in_detail_response(self):

        created = wr.create_wizard(
            WizardCreateRequest(creation_idempotency_key=_key("k")),
            current_user=self.user_a, db=self.db,
        )

        updated = wr.update_draft(
            created["id"],
            WizardDraftUpdateRequest(
                expected_version=created["version"], product_name="테스트상품",
                brand="HOMEZ",
            ),
            current_user=self.user_a, db=self.db,
        )

        self.assertEqual(updated["draft"]["product_name"], "테스트상품")
        self.assertEqual(updated["draft"]["brand"], "HOMEZ")
        self.assertEqual(updated["current_step"], "MEDIA")

    def test_list_wizards_scoped_to_company(self):

        wr.create_wizard(
            WizardCreateRequest(creation_idempotency_key=_key("k")),
            current_user=self.user_a, db=self.db,
        )
        wr.create_wizard(
            WizardCreateRequest(creation_idempotency_key=_key("k")),
            current_user=self.user_b, db=self.db,
        )

        a_list = wr.list_wizards(
            status=None, limit=100, offset=0,
            current_user=self.user_a, db=self.db,
        )
        b_list = wr.list_wizards(
            status=None, limit=100, offset=0,
            current_user=self.user_b, db=self.db,
        )

        self.assertEqual(len(a_list), 1)
        self.assertEqual(len(b_list), 1)
        self.assertNotEqual(a_list[0].id, b_list[0].id)


class ListingWizardAutoUploadImagesRouterTestCase(unittest.TestCase):
    """2026-08-29 R2 이미지 자동 업로드 엔드포인트(POST /{wizard_id}/
    coupang/auto-upload-images) — 이 라우터 함수는 얇은 위임
    래퍼이므로, autofill_coupang_images() 자체의 전체 시나리오는
    tests/test_coupang_image_autofill.py가 이미 전담 검증한다. 여기서는
    (1) 선택된 이미지가 없을 때 차단, (2) 위저드에 저장된
    selected_media_asset_ids로 올바르게 위임·커밋하는지만 본다."""

    def setUp(self):

        clear_all_wizard_approval_nonces()

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
            ],
        )
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        SafetyService(self.db).set_mode(
            AutomationMode.OPERATOR_APPROVAL, set_by=1, is_admin=True,
        )

        company = Company(
            name="A", business_number="1", ceo="t", phone="t",
            email="a@example.com", address="t",
        )
        self.db.add(company)
        self.db.commit()

        self.user = types.SimpleNamespace(
            id=1, company_id=company.id, role="ADMIN",
        )

        self.media = MediaAsset(
            company_id=company.id, owner_type="PRODUCT_CANDIDATE", owner_id=1,
            asset_role="ORIGINAL", purpose="MAIN", display_order=0,
            storage_path="1/seed.jpg", mime_type="image/jpeg",
            file_size_bytes=100, sha256_hex="a" * 64, status="ACTIVE",
            rights_status="VERIFIED",
        )
        self.db.add(self.media)
        self.db.commit()

        self.wizard = wr.create_wizard(
            WizardCreateRequest(creation_idempotency_key=_key("k")),
            current_user=self.user, db=self.db,
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        clear_all_wizard_approval_nonces()

    def test_no_selected_images_raises_bad_request(self):

        with self.assertRaisesRegex(BadRequestException, "3단계"):
            wr.auto_upload_coupang_images(
                self.wizard["id"], current_user=self.user, db=self.db,
                credential_store=InMemoryCredentialStore(),
            )

    def test_delegates_to_autofill_with_selected_media_and_commits(self):

        wr.update_media(
            self.wizard["id"],
            WizardMediaUpdateRequest(
                expected_version=self.wizard["version"],
                selected_media_asset_ids=[self.media.id],
            ),
            current_user=self.user, db=self.db,
        )

        fake_images = [{
            "imageOrder": 0, "imageType": "REPRESENTATION",
            "vendorPath": "https://pub-x.r2.dev/seed.jpg",
        }]
        store = InMemoryCredentialStore()
        with patch(
            "app.domains.marketplace_listing.coupang_image_autofill."
            "autofill_coupang_images",
            return_value=fake_images,
        ) as fake_autofill:
            result = wr.auto_upload_coupang_images(
                self.wizard["id"], current_user=self.user, db=self.db,
                credential_store=store,
            )

        fake_autofill.assert_called_once_with(
            self.db, self.user.company_id, [self.media.id], store,
        )
        self.assertEqual(result, {"images": fake_images})

    def test_cross_company_wizard_raises_not_found(self):

        other_user = types.SimpleNamespace(id=2, company_id=9999, role="ADMIN")

        with self.assertRaises(NotFoundException):
            wr.auto_upload_coupang_images(
                self.wizard["id"], current_user=other_user, db=self.db,
                credential_store=InMemoryCredentialStore(),
            )


class ListingWizardContentsFromMediaRouterTestCase(unittest.TestCase):
    """2026-08-31 — POST /{wizard_id}/coupang/contents-from-media
    (required_fields.contents 자동 구성). auto-upload-images와 동일한
    구조 — 이 라우터 함수는 얇은 위임 래퍼이므로,
    build_contents_from_media_assets() 자체의 시나리오는
    tests/test_coupang_contents_builder.py가 전담 검증한다."""

    def setUp(self):

        clear_all_wizard_approval_nonces()

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
            ],
        )
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        SafetyService(self.db).set_mode(
            AutomationMode.OPERATOR_APPROVAL, set_by=1, is_admin=True,
        )

        company = Company(
            name="A", business_number="1", ceo="t", phone="t",
            email="a@example.com", address="t",
        )
        self.db.add(company)
        self.db.commit()

        self.user = types.SimpleNamespace(
            id=1, company_id=company.id, role="ADMIN",
        )

        self.media = MediaAsset(
            company_id=company.id, owner_type="PRODUCT_CANDIDATE", owner_id=1,
            asset_role="GENERATED", purpose="DETAIL", display_order=0,
            storage_path="1/detail.jpg", mime_type="image/jpeg",
            file_size_bytes=100, sha256_hex="a" * 64, status="ACTIVE",
            rights_status="VERIFIED",
        )
        self.db.add(self.media)
        self.db.commit()

        self.wizard = wr.create_wizard(
            WizardCreateRequest(creation_idempotency_key=_key("k")),
            current_user=self.user, db=self.db,
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        clear_all_wizard_approval_nonces()

    def test_empty_selection_raises_bad_request(self):

        with self.assertRaisesRegex(BadRequestException, "최소 1개"):
            wr.build_coupang_contents_from_media(
                self.wizard["id"],
                CoupangContentsFromMediaRequest(media_asset_ids=[]),
                current_user=self.user, db=self.db,
                credential_store=InMemoryCredentialStore(),
            )

    def test_delegates_to_builder_with_selected_media_and_commits(self):

        fake_contents = [{
            "contentsType": "IMAGE",
            "contentDetails": [
                {"detailType": "IMAGE", "content": '<img src="https://pub-x.r2.dev/detail.jpg">'},
            ],
        }]
        store = InMemoryCredentialStore()
        with patch(
            "app.domains.marketplace_listing.coupang_contents_builder."
            "build_contents_from_media_assets",
            return_value=fake_contents,
        ) as fake_builder:
            result = wr.build_coupang_contents_from_media(
                self.wizard["id"],
                CoupangContentsFromMediaRequest(media_asset_ids=[self.media.id]),
                current_user=self.user, db=self.db,
                credential_store=store,
            )

        fake_builder.assert_called_once_with(
            self.db, self.user.company_id, self.wizard["product_candidate_id"],
            [self.media.id], store,
        )
        self.assertEqual(result, {"contents": fake_contents})

    def test_cross_company_wizard_raises_not_found(self):

        other_user = types.SimpleNamespace(id=2, company_id=9999, role="ADMIN")

        with self.assertRaises(NotFoundException):
            wr.build_coupang_contents_from_media(
                self.wizard["id"],
                CoupangContentsFromMediaRequest(media_asset_ids=[self.media.id]),
                current_user=other_user, db=self.db,
                credential_store=InMemoryCredentialStore(),
            )


class ListingWizardSoftDeleteRouterTestCase(unittest.TestCase):
    """2026-08-28 "대기 상품 정리" — 삭제/복원/일괄삭제 라우터 함수를
    실제 Session·User로 직접 호출한다(TestClient 대체, 이 파일 상단의
    기존 관례와 동일)."""

    def setUp(self):

        reset_recent_auth_state_for_tests()

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[Company.__table__, ListingWizard.__table__],
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

        company_a = Company(
            name="A", business_number=_key("bn"), ceo="t", phone="t",
            email=f"{_key('a')}@example.com", address="t",
        )
        company_b = Company(
            name="B", business_number=_key("bn"), ceo="t", phone="t",
            email=f"{_key('b')}@example.com", address="t",
        )
        self.db.add_all([company_a, company_b])
        self.db.commit()

        self.user_a = types.SimpleNamespace(
            id=1, company_id=company_a.id, role="ADMIN",
        )
        self.user_b = types.SimpleNamespace(
            id=2, company_id=company_b.id, role="ADMIN",
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        reset_recent_auth_state_for_tests()

    def _seed_wizard(self, company_id: int):

        wizard = ListingWizard(
            company_id=company_id, created_by_user_id=1,
            current_step="SOURCE", status="DRAFT", source_type="MANUAL",
            creation_idempotency_key=_key("k"),
        )
        self.db.add(wizard)
        self.db.commit()
        return wizard

    def test_delete_then_restore_round_trip_via_router_functions(self):

        wizard = self._seed_wizard(self.user_a.company_id)

        deleted = wr.delete_wizard(
            wizard.id,
            WizardDeleteRequest(
                expected_version=wizard.version, reason="테스트 삭제",
                deletion_request_id=_key("del"),
            ),
            current_user=self.user_a, db=self.db,
        )
        self.assertEqual(deleted["status"], "ARCHIVED")

        restored = wr.restore_wizard(
            wizard.id,
            WizardRestoreRequest(expected_version=deleted["version"]),
            current_user=self.user_a, db=self.db,
        )
        self.assertEqual(restored["status"], "DRAFT")

    def test_delete_cross_company_raises_not_found(self):

        wizard = self._seed_wizard(self.user_a.company_id)

        with self.assertRaises(NotFoundException):
            wr.delete_wizard(
                wizard.id,
                WizardDeleteRequest(
                    expected_version=wizard.version, reason="타사 시도",
                    deletion_request_id=_key("del"),
                ),
                current_user=self.user_b, db=self.db,
            )

    def test_bulk_archive_select_all_without_recent_auth_is_forbidden(self):

        self._seed_wizard(self.user_a.company_id)

        with self.assertRaises(ForbiddenException):
            wr.bulk_archive_wizards(
                WizardBulkArchiveRequest(
                    select_all_matching_filter=True, confirm_text="삭제",
                    reason="recent-auth 없음", deletion_request_id=_key("bulk"),
                ),
                current_user=self.user_a, recent_auth_token=None, db=self.db,
            )

    def test_bulk_archive_select_all_with_recent_auth_succeeds(self):

        wizard = self._seed_wizard(self.user_a.company_id)
        token, _expires_at = issue_recent_auth_token(self.user_a.id)

        response = wr.bulk_archive_wizards(
            WizardBulkArchiveRequest(
                select_all_matching_filter=True, confirm_text="삭제",
                reason="recent-auth 있음", deletion_request_id=_key("bulk"),
            ),
            current_user=self.user_a, recent_auth_token=token, db=self.db,
        )

        self.assertEqual(response.succeeded, [wizard.id])

    def test_bulk_archive_explicit_ids_does_not_require_recent_auth(self):
        """"선택 삭제"(현재 페이지에서 명시적으로 고른 항목)는
        recent-auth 없이도 통과해야 한다 — recent-auth는
        select_all_matching_filter 모드에만 붙는다."""

        wizard = self._seed_wizard(self.user_a.company_id)

        response = wr.bulk_archive_wizards(
            WizardBulkArchiveRequest(
                wizard_ids=[wizard.id], reason="선택 삭제",
                deletion_request_id=_key("bulk"),
            ),
            current_user=self.user_a, recent_auth_token=None, db=self.db,
        )

        self.assertEqual(response.succeeded, [wizard.id])

    def test_bulk_archive_preview_via_router_function(self):

        self._seed_wizard(self.user_a.company_id)

        preview = wr.bulk_archive_preview(
            status=None, current_user=self.user_a, db=self.db,
        )
        self.assertEqual(preview.deletable_count, 1)


if __name__ == "__main__":
    unittest.main()
