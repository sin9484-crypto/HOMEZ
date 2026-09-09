"""
=========================================================
Homez OS

File : tests/test_onchannel_credential_endpoints.py

2026-09-08 V7 통합 매입 순서 6번 — 온채널 API 인증키 등록 엔드포인트
검증. 라우터 함수를 FastAPI 의존성 주입 없이 직접 호출한다(NAVER
API HUB credential 엔드포인트 테스트와 동일한 이 저장소의 관례).
InMemoryCredentialStore만 사용 — 실제 Windows Credential Manager는
건드리지 않는다.
=========================================================
"""

from __future__ import annotations

import unittest

from app.core.windows_credential_store import InMemoryCredentialStore
from app.domains.purchase.router import get_onchannel_credential_status
from app.domains.purchase.router import save_onchannel_credential
from app.domains.purchase.schema import OnchannelCredentialSaveRequest
from app.domains.purchase.supplier_order_providers import (
    OnchannelSupplierOrderProvider,
)


class _FakeUser:
    id = 1
    company_id = 1


class OnchannelCredentialEndpointsTest(unittest.TestCase):

    def setUp(self):
        self.store = InMemoryCredentialStore()

    def test_status_reports_not_registered_initially(self):
        status = get_onchannel_credential_status(
            current_user=_FakeUser(), credential_store=self.store,
        )
        self.assertFalse(status.registered)
        self.assertIsNone(status.allowed_ip)

    def test_save_then_status_reports_registered_with_ip(self):
        save_onchannel_credential(
            OnchannelCredentialSaveRequest(
                auth_key="my-jwt-token", allowed_ip="1.2.3.4",
            ),
            current_user=_FakeUser(), credential_store=self.store,
        )

        status = get_onchannel_credential_status(
            current_user=_FakeUser(), credential_store=self.store,
        )
        self.assertTrue(status.registered)
        self.assertEqual(status.allowed_ip, "1.2.3.4")

        saved = self.store.read(OnchannelSupplierOrderProvider.CREDENTIAL_REFERENCE)
        self.assertEqual(saved["auth_key"], "my-jwt-token")
        self.assertEqual(saved["allowed_ip"], "1.2.3.4")

    def test_status_response_never_includes_key_value(self):
        save_onchannel_credential(
            OnchannelCredentialSaveRequest(
                auth_key="super-secret-jwt-value", allowed_ip="",
            ),
            current_user=_FakeUser(), credential_store=self.store,
        )
        status = get_onchannel_credential_status(
            current_user=_FakeUser(), credential_store=self.store,
        )
        self.assertNotIn("super-secret-jwt-value", repr(status))

    def test_allowed_ip_optional(self):
        save_onchannel_credential(
            OnchannelCredentialSaveRequest(auth_key="my-jwt-token"),
            current_user=_FakeUser(), credential_store=self.store,
        )
        status = get_onchannel_credential_status(
            current_user=_FakeUser(), credential_store=self.store,
        )
        self.assertTrue(status.registered)
        self.assertIsNone(status.allowed_ip)


if __name__ == "__main__":
    unittest.main()
