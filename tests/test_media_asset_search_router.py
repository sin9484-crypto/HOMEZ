"""
=========================================================
Homez OS

File : tests/test_media_asset_search_router.py

2026-09-07 — POST /media-assets/search 라우터 함수 자체를 검증한다
(기존에는 이 엔드포인트에 대한 라우터 레벨 테스트가 전혀 없었다 —
FAKE Provider 회귀와 오늘 새로 UI 기본값이 된 NAVER Provider 응답
매핑을 함께 확인한다). HTTP 서버를 띄우지 않고 라우터 함수를
직접 호출한다(이 저장소의 다른 여러 라우터 테스트와 동일한 패턴).

중요: PROVIDERS_BY_CODE["NAVER"]는 앱 기동 시 실제 Windows
Credential Manager(WindowsCredentialStore)를 사용하도록 만들어진
전역 싱글턴이다 — 이 자격증명 스토어를 테스트에서 절대 건드리면
안 된다(개발자 PC의 실제 "homez_naver_api_hub" 키를 테스트 값으로
덮어쓰게 됨). 그래서 테스트 동안만 PROVIDERS_BY_CODE["NAVER"] 자체를
InMemoryCredentialStore 기반의 별도 인스턴스로 완전히 교체하고,
끝나면 원래 싱글턴으로 반드시 복원한다.
=========================================================
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.core.api_usage_tracker import ApiUsageTracker
from app.core.windows_credential_store import InMemoryCredentialStore
from app.domains.media_asset import router as media_asset_router
from app.domains.media_asset.image_search_providers import NaverImageSearchProvider
from app.domains.media_asset.image_search_providers import PROVIDERS_BY_CODE
from app.domains.media_asset.schema import ImageSearchQueryRequest


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _StubUser:
    id = 1
    company_id = 1


class MediaAssetSearchRouterTest(unittest.TestCase):

    def test_fake_provider_still_works(self):
        results = media_asset_router.search_images(
            ImageSearchQueryRequest(product_name="테스트상품", provider_code="FAKE"),
            current_user=_StubUser(),
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].provider_code, "FAKE")
        self.assertFalse(results[0].selectable)

    def test_naver_provider_maps_response_with_preview_image_and_not_selectable(self):

        def fake_get(url, headers, params, timeout):
            return _FakeResponse(200, {
                "items": [{
                    "title": "라우터 테스트 상품",
                    "link": "https://example.test/router-test",
                    "thumbnail": "https://example.test/router-test-thumb.jpg",
                }],
            })

        isolated_store = InMemoryCredentialStore()
        isolated_store.save("router-test-cred", {
            "client_id": "router-test-id", "client_secret": "router-test-secret",
        })
        fd, usage_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(usage_path)
        test_provider = NaverImageSearchProvider(
            isolated_store, "router-test-cred", http_get=fake_get,
            usage_tracker=ApiUsageTracker(Path(usage_path)),
        )

        original_provider = PROVIDERS_BY_CODE["NAVER"]
        PROVIDERS_BY_CODE["NAVER"] = test_provider
        try:
            results = media_asset_router.search_images(
                ImageSearchQueryRequest(product_name="라우터테스트", provider_code="NAVER"),
                current_user=_StubUser(),
            )
        finally:
            PROVIDERS_BY_CODE["NAVER"] = original_provider
            if os.path.exists(usage_path):
                os.remove(usage_path)

        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertEqual(item.provider_code, "NAVER")
        self.assertEqual(item.preview_image_url, "https://example.test/router-test-thumb.jpg")
        self.assertEqual(item.permission_status, "UNKNOWN")
        self.assertFalse(item.selectable)  # UNKNOWN은 SELECTABLE에 없음

    def test_unknown_provider_code_raises_bad_request(self):
        from app.core.exceptions import BadRequestException

        with self.assertRaises(BadRequestException):
            media_asset_router.search_images(
                ImageSearchQueryRequest(product_name="아무거나", provider_code="NOT_A_PROVIDER"),
                current_user=_StubUser(),
            )


if __name__ == "__main__":
    unittest.main()
