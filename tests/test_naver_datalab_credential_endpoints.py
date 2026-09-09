"""
=========================================================
Homez OS

File : tests/test_naver_datalab_credential_endpoints.py

2026-09-06 — 네이버 데이터랩 Client ID/Secret 등록 엔드포인트 검증.
라우터 함수를 FastAPI 의존성 주입 없이 직접 호출한다(이 저장소의
다른 라우터 테스트들과 달리 이 프로젝트에는 TestClient 기반 테스트
관례가 없어, 서비스/함수 레벨 직접 호출 방식을 그대로 따른다).
InMemoryCredentialStore만 사용 — 실제 Windows Credential Manager는
건드리지 않는다.
=========================================================
"""

from __future__ import annotations

import unittest

from app.core.windows_credential_store import InMemoryCredentialStore
from app.domains.product_candidate.router import (
    get_naver_datalab_credential_status,
)
from app.domains.product_candidate.router import save_naver_datalab_credential
from app.domains.product_candidate.schema import (
    NaverDataLabCredentialSaveRequest,
)
from app.domains.trend_discovery.adapter import NaverDataLabTrendAdapter


class _FakeUser:
    id = 1
    company_id = 1


class NaverDataLabCredentialEndpointsTest(unittest.TestCase):

    def setUp(self):
        self.store = InMemoryCredentialStore()

    def test_status_reports_not_registered_initially(self):
        status = get_naver_datalab_credential_status(
            current_user=_FakeUser(), credential_store=self.store,
        )
        self.assertFalse(status.registered)

    def test_save_then_status_reports_registered(self):
        save_naver_datalab_credential(
            NaverDataLabCredentialSaveRequest(
                client_id="my-id", client_secret="my-secret",
            ),
            current_user=_FakeUser(), credential_store=self.store,
        )

        status = get_naver_datalab_credential_status(
            current_user=_FakeUser(), credential_store=self.store,
        )
        self.assertTrue(status.registered)

        saved = self.store.read(NaverDataLabTrendAdapter.DEFAULT_CREDENTIAL_REFERENCE)
        self.assertEqual(saved["client_id"], "my-id")
        self.assertEqual(saved["client_secret"], "my-secret")

    def test_status_response_never_includes_secret_value(self):
        save_naver_datalab_credential(
            NaverDataLabCredentialSaveRequest(
                client_id="my-id", client_secret="super-secret-value",
            ),
            current_user=_FakeUser(), credential_store=self.store,
        )
        status = get_naver_datalab_credential_status(
            current_user=_FakeUser(), credential_store=self.store,
        )
        self.assertNotIn("super-secret-value", repr(status))


if __name__ == "__main__":
    unittest.main()
