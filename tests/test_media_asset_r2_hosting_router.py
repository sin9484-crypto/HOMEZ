"""
=========================================================
Homez OS

File : tests/test_media_asset_r2_hosting_router.py

app/domains/media_asset/router.py의 R2 공개 호스팅 자격증명
엔드포인트(POST/GET /media-assets/r2-hosting/*) 검증. 이 저장소는
httpx가 없어 FastAPI TestClient를 쓰지 않는다(tests/test_listing_
wizard_router.py 등과 동일 관례) — 라우터 함수를 직접 호출해
(1) admin_guard 배선을, (2) Credential 저장·조회 왕복 동작을 확인한다.
실제 Windows Credential Manager는 절대 건드리지 않는다 — 항상
InMemoryCredentialStore를 credential_store 파라미터로 직접 주입한다
(get_r2_credential_store 의존성 자체는 배선 확인만 하고 호출하지
않는다).
=========================================================
"""

import unittest

from app.core.guard import admin_guard
from app.core.windows_credential_store import InMemoryCredentialStore
from app.domains.media_asset import router as mar
from app.domains.media_asset import schema


class R2HostingRouterWiringTestCase(unittest.TestCase):
    """실제 FastAPI Dependant 체인을 읽어 admin_guard 배선을 확인한다."""

    def _route(self, path: str, method: str):

        return next(
            r for r in mar.router.routes
            if r.path == path and method in r.methods
        )

    def test_save_credentials_requires_admin_guard(self):

        route = self._route("/media-assets/r2-hosting/credentials", "POST")
        calls = [d.call for d in route.dependant.dependencies]
        self.assertIn(admin_guard, calls)

    def test_status_requires_admin_guard(self):

        route = self._route("/media-assets/r2-hosting/status", "GET")
        calls = [d.call for d in route.dependant.dependencies]
        self.assertIn(admin_guard, calls)

    def test_get_r2_credential_store_default_is_windows_credential_manager(self):
        """운영 코드 자신은 절대 자동으로 in-memory를 선택하지 않는다
        — 기본 의존성이 실제 WindowsCredentialStore를 반환하는지만
        타입으로 확인한다(실제 시스템 저장소에 쓰지는 않는다)."""

        from app.core.windows_credential_store import WindowsCredentialStore

        store = mar.get_r2_credential_store()
        self.assertIsInstance(store, WindowsCredentialStore)


class R2HostingRouterFunctionalTestCase(unittest.TestCase):
    """라우터 함수를 InMemoryCredentialStore로 직접 호출한다."""

    def setUp(self):

        self.store = InMemoryCredentialStore()
        self.user = object()

    def test_status_is_not_configured_before_save(self):

        status = mar.get_r2_hosting_status(
            current_user=self.user, credential_store=self.store,
        )
        self.assertFalse(status.configured)

    def test_save_then_status_reports_configured(self):

        result = mar.save_r2_hosting_credentials(
            schema.R2CredentialsSaveRequest(
                account_id="acct123", access_key_id="AKIA...",
                secret_access_key="secret...", bucket_name="homez-images",
                public_base_url="https://pub-xxxx.r2.dev",
            ),
            current_user=self.user, credential_store=self.store,
        )
        self.assertTrue(result.configured)

        status = mar.get_r2_hosting_status(
            current_user=self.user, credential_store=self.store,
        )
        self.assertTrue(status.configured)

    def test_save_rejects_unsupported_extra_field(self):

        with self.assertRaises(Exception):
            schema.R2CredentialsSaveRequest(
                account_id="acct123", access_key_id="AKIA...",
                secret_access_key="secret...", bucket_name="homez-images",
                public_base_url="https://pub-xxxx.r2.dev",
                unexpected_field="x",
            )

    def test_save_rejects_missing_required_field(self):

        with self.assertRaises(Exception):
            schema.R2CredentialsSaveRequest(
                account_id="acct123", access_key_id="AKIA...",
                secret_access_key="secret...", bucket_name="homez-images",
            )


if __name__ == "__main__":
    unittest.main()
