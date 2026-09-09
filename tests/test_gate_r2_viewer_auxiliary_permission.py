"""
=========================================================
Homez OS

File : tests/test_gate_r2_viewer_auxiliary_permission.py

Gate R-2(2026-08-09) — 상품등록 통합 마법사가 화면을 그리는 데 필요한
보조 조회 API(채널·계정·자격·미디어 자산) 4개를 admin_guard에서
LISTING_WIZARD_VIEW로 전환했다. 이 파일은:
1) 라우터 배선(route.dependant) 수준에서 4개 읽기 엔드포인트가 더
   이상 admin_guard가 아님을, 그리고 같은 파일의 쓰기 엔드포인트는
   여전히 admin_guard임을 확인한다.
2) 실제로 라우트에 연결된 Guard 함수 객체를 그대로 호출해(가짜로
   새로 만들지 않고) VIEWER가 권한 없이는 403, 권한을 부여하면
   통과함을 확인한다.
3) 응답 스키마에 Credential·Secret·Provider 원문 필드가 없음을
   확인한다.

전부 임시 SQLite DB에서만 검증한다 — 실제 homez.db에는 Permission
행을 만들지 않는다.
=========================================================
"""

import os
import tempfile
import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.guard import admin_guard
from app.core.security import hash_password
from app.database.base import Base
from app.domains.marketplace_listing import router as ml_router
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_VIEW,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceAccountResponse,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceChannelResponse,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceFulfillmentCapabilityResponse,
)
from app.domains.media_asset import router as media_router
from app.domains.media_asset.schema import MediaAssetResponse
from app.domains.company.model import Company
from app.domains.permission.model import Permission
from app.domains.role.model import Role
from app.domains.role_permission.model import RolePermission
from app.domains.user.model import User


class GateR2RouterWiringTestCase(unittest.TestCase):
    """읽기 4개는 Permission 기반, 쓰기는 그대로 admin_guard인지
    정적으로 확인한다."""

    def _route(self, router, path, method):

        return next(
            r for r in router.routes
            if r.path == path and method in r.methods
        )

    def _calls(self, route):

        return [d.call for d in route.dependant.dependencies]

    def test_read_endpoints_use_permission_guard_not_admin_guard(self):

        cases = [
            (ml_router.router, "/marketplace-listings/channels", "GET"),
            (ml_router.router, "/marketplace-listings/channels/{channel_id}/capabilities", "GET"),
            (ml_router.router, "/marketplace-listings/channels/{channel_id}/accounts", "GET"),
            (media_router.router, "/media-assets/owners/{owner_type}/{owner_id}/assets", "GET"),
        ]
        for router, path, method in cases:
            calls = self._calls(self._route(router, path, method))
            self.assertNotIn(admin_guard, calls, path)
            self.assertTrue(
                any(
                    getattr(c, "__qualname__", "").startswith(
                        "ListingWizardPermissionGuard.",
                    )
                    for c in calls
                ),
                path,
            )

    def test_write_endpoints_remain_admin_guard(self):

        cases = [
            (ml_router.router, "/marketplace-listings/channels/seed-capability-registry", "POST"),
            (ml_router.router, "/marketplace-listings/capabilities/{capability_id}/verify", "POST"),
            (ml_router.router, "/marketplace-listings/accounts", "POST"),
            (media_router.router, "/media-assets/image-jobs", "POST"),
            (media_router.router, "/media-assets/image-jobs/{job_id}/cancel", "POST"),
            (media_router.router, "/media-assets/image-jobs/{job_id}/retry", "POST"),
        ]
        for router, path, method in cases:
            calls = self._calls(self._route(router, path, method))
            self.assertIn(admin_guard, calls, path)

    def test_response_schemas_never_expose_credential_shaped_fields(self):

        forbidden_substrings = (
            "secret", "credential", "access_token", "refresh_token",
            "api_key", "raw_response",
        )
        for schema_cls in (
            MarketplaceChannelResponse, MarketplaceAccountResponse,
            MarketplaceFulfillmentCapabilityResponse, MediaAssetResponse,
        ):
            for field_name in schema_cls.model_fields:
                lowered = field_name.lower()
                for bad in forbidden_substrings:
                    self.assertNotIn(
                        bad, lowered,
                        f"{schema_cls.__name__}.{field_name} 이(가) "
                        f"credential-shaped 필드로 의심됩니다.",
                    )


class GateR2GuardBehaviorTestCase(unittest.TestCase):
    """라우트에 실제로 연결된 Guard 함수 객체를 그대로 호출한다."""

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, Role.__table__, Permission.__table__,
                RolePermission.__table__, User.__table__,
            ],
        )
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = SessionLocal()

        self.company = Company(
            name="R2 Co", business_number="r2-1", ceo="t", phone="t",
            email="r2@example.com", address="t",
        )
        self.db.add(self.company)
        self.db.commit()

        self.viewer_role = Role(name="Viewer", code="VIEWER")
        self.db.add(self.viewer_role)
        self.db.commit()

        self.viewer_user = User(
            username="r2viewer", email="r2viewer@example.com",
            password_hash=hash_password("Str0ng!Passw0rd"),
            role_id=self.viewer_role.id, company_id=self.company.id,
            is_active=True,
        )
        self.db.add(self.viewer_user)
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _guard_for(self, router, path, method):

        route = next(
            r for r in router.routes
            if r.path == path and method in r.methods
        )
        for dep in route.dependant.dependencies:
            if getattr(dep.call, "__qualname__", "").startswith(
                "ListingWizardPermissionGuard.",
            ):
                return dep.call
        raise AssertionError(f"{path}: PermissionGuard를 찾지 못했습니다.")

    def test_viewer_without_grant_denied_on_all_four_read_endpoints(self):

        cases = [
            (ml_router.router, "/marketplace-listings/channels", "GET"),
            (ml_router.router, "/marketplace-listings/channels/{channel_id}/capabilities", "GET"),
            (ml_router.router, "/marketplace-listings/channels/{channel_id}/accounts", "GET"),
            (media_router.router, "/media-assets/owners/{owner_type}/{owner_id}/assets", "GET"),
        ]
        for router, path, method in cases:
            guard = self._guard_for(router, path, method)
            with self.assertRaises(HTTPException) as ctx:
                guard(current_user=self.viewer_user, db=self.db)
            self.assertEqual(ctx.exception.status_code, 403, path)

    def test_viewer_with_listing_wizard_view_grant_passes_all_four(self):

        perm = Permission(
            name=LISTING_WIZARD_VIEW, code=LISTING_WIZARD_VIEW, active=True,
        )
        self.db.add(perm)
        self.db.commit()
        self.db.add(RolePermission(
            role_id=self.viewer_role.id, permission_id=perm.id,
        ))
        self.db.commit()

        cases = [
            (ml_router.router, "/marketplace-listings/channels", "GET"),
            (ml_router.router, "/marketplace-listings/channels/{channel_id}/capabilities", "GET"),
            (ml_router.router, "/marketplace-listings/channels/{channel_id}/accounts", "GET"),
            (media_router.router, "/media-assets/owners/{owner_type}/{owner_id}/assets", "GET"),
        ]
        for router, path, method in cases:
            guard = self._guard_for(router, path, method)
            result = guard(current_user=self.viewer_user, db=self.db)
            self.assertEqual(result.id, self.viewer_user.id, path)


if __name__ == "__main__":
    unittest.main()
