"""
=========================================================
Homez OS

File : tests/test_naver_image_search_provider.py

2026-09-07 — NaverImageSearchProvider 검증. 실제 네트워크는 절대
호출하지 않는다 — http_get 주입 지점을 통해 항상 가짜 응답 객체로
대체한다. 사용량 카운터도 항상 임시 파일 경로를 명시적으로 주입한다.
=========================================================
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.core.api_usage_tracker import ApiUsageTracker
from app.core.windows_credential_store import InMemoryCredentialStore
from app.domains.media_asset.image_search_providers import ImagePermissionStatus
from app.domains.media_asset.image_search_providers import ImageSearchQuery
from app.domains.media_asset.image_search_providers import NaverImageSearchProvider
from app.domains.media_asset.image_search_providers import PROVIDERS_BY_CODE
from app.domains.trend_discovery.adapter import NaverDataLabTrendAdapter


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class NaverImageSearchProviderTest(unittest.TestCase):

    CRED_NAME = "naver-image-test"

    def setUp(self):
        self.store = InMemoryCredentialStore()
        self.store.save(self.CRED_NAME, {
            "client_id": "test-client-id", "client_secret": "test-secret",
        })
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        self.usage_path = Path(path)
        self.usage_tracker = ApiUsageTracker(self.usage_path)

    def tearDown(self):
        if self.usage_path.exists():
            self.usage_path.unlink()
        tmp = self.usage_path.with_suffix(".tmp")
        if tmp.exists():
            tmp.unlink()

    def _provider(self, http_get):
        return NaverImageSearchProvider(
            self.store, self.CRED_NAME, http_get=http_get,
            usage_tracker=self.usage_tracker,
        )

    def test_registered_under_naver_code_with_shared_credential_reference(self):
        provider = PROVIDERS_BY_CODE["NAVER"]
        self.assertEqual(
            provider.credential_reference,
            NaverDataLabTrendAdapter.DEFAULT_CREDENTIAL_REFERENCE,
        )

    def test_successful_search_returns_items_marked_unknown_permission(self):
        captured = {}

        def fake_get(url, headers, params, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["params"] = params
            return _FakeResponse(200, {
                "items": [
                    {
                        "title": "무선청소기 정품",
                        "link": "https://example.test/product/1",
                        "thumbnail": "https://example.test/thumb/1.jpg",
                        "sizeheight": "300",
                        "sizewidth": "300",
                    },
                ],
            })

        results = self._provider(fake_get).search(
            ImageSearchQuery(product_name="무선청소기"),
        )

        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertEqual(item.title, "무선청소기 정품")
        self.assertEqual(item.preview_image_url, "https://example.test/thumb/1.jpg")
        self.assertEqual(item.permission_status, ImagePermissionStatus.UNKNOWN)
        self.assertEqual(item.match_status, "UNCERTAIN")
        self.assertEqual(item.provider_code, "NAVER")

        self.assertIn("naverapihub.apigw.ntruss.com", captured["url"])
        self.assertEqual(captured["headers"]["X-NCP-APIGW-API-KEY-ID"], "test-client-id")
        self.assertEqual(captured["headers"]["X-NCP-APIGW-API-KEY"], "test-secret")
        self.assertEqual(captured["params"]["query"], "무선청소기")

    def test_empty_product_name_returns_empty_without_network_call(self):
        called = []

        def fake_get(**kwargs):
            called.append(True)
            return _FakeResponse(200, {"items": []})

        results = self._provider(fake_get).search(
            ImageSearchQuery(product_name=""),
        )
        self.assertEqual(results, [])
        self.assertEqual(called, [])

    def test_missing_credential_returns_empty_list(self):
        empty_store = InMemoryCredentialStore()
        provider = NaverImageSearchProvider(
            empty_store, "does-not-exist", http_get=lambda **kw: None,
            usage_tracker=self.usage_tracker,
        )
        results = provider.search(ImageSearchQuery(product_name="아무거나"))
        self.assertEqual(results, [])

    def test_non_200_status_returns_empty_list(self):
        provider = self._provider(
            lambda url, headers, params, timeout: _FakeResponse(400, {}),
        )
        results = provider.search(ImageSearchQuery(product_name="오류테스트"))
        self.assertEqual(results, [])

    def test_network_exception_returns_empty_list_not_raised(self):
        def raising_get(**kwargs):
            raise ConnectionError("네트워크 끊김")

        provider = self._provider(raising_get)
        results = provider.search(ImageSearchQuery(product_name="네트워크오류"))
        self.assertEqual(results, [])

    def test_malformed_item_is_skipped_not_fatal(self):
        def fake_get(url, headers, params, timeout):
            return _FakeResponse(200, {
                "items": [
                    {"title": "필드 누락"},  # link/thumbnail 없음
                    {
                        "title": "정상 항목",
                        "link": "https://example.test/2",
                        "thumbnail": "https://example.test/thumb/2.jpg",
                    },
                ],
            })

        provider = self._provider(fake_get)
        results = provider.search(ImageSearchQuery(product_name="부분오류"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "정상 항목")

    def test_daily_limit_reached_blocks_call_without_network(self):
        import json as _json
        from datetime import date as _date

        called = []

        def fake_get(**kwargs):
            called.append(True)
            return _FakeResponse(200, {"items": []})

        today = _date.today()
        self.usage_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.usage_path, "w", encoding="utf-8") as f:
            _json.dump({
                NaverImageSearchProvider.USAGE_BUCKET: {
                    "daily": {
                        "date": today.isoformat(),
                        "count": NaverImageSearchProvider.DAILY_CALL_LIMIT,
                    },
                    "monthly": {
                        "month": f"{today.year:04d}-{today.month:02d}",
                        "count": 1,
                    },
                },
            }, f)

        results = self._provider(fake_get).search(
            ImageSearchQuery(product_name="일일한도초과"),
        )
        self.assertEqual(results, [])
        self.assertEqual(called, [])

    def test_successful_call_records_usage(self):
        provider = self._provider(
            lambda url, headers, params, timeout: _FakeResponse(200, {"items": []}),
        )
        provider.search(ImageSearchQuery(product_name="사용량기록"))
        check = self.usage_tracker.is_within_limit(
            NaverImageSearchProvider.USAGE_BUCKET,
            daily_limit=None, monthly_limit=None,
        )
        self.assertEqual(check.daily_count, 1)


if __name__ == "__main__":
    unittest.main()
