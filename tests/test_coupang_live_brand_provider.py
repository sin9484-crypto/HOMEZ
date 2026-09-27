"""
=========================================================
Homez OS

File : tests/test_coupang_live_brand_provider.py

2026-09-27 — CoupangLiveBrandProvider(실제 쿠팡 브랜드 검색 API 연동)
파싱 계약 검증. 공식 문서(Brand Search API, developers.coupang.com,
2026-09-27 fetch) 응답 예시 그대로의 fixture를 사용한다. `_request`만
monkey-patch해 실 네트워크·인증 서명 자체는 건드리지 않는다(기존
CoupangCategoryMetadataProvider 테스트와 동일 관례).
=========================================================
"""

import unittest

from app.domains.marketplace_listing.coupang_brand_provider import (
    CoupangBrandProviderError,
    CoupangLiveBrandProvider,
)


class CoupangLiveBrandProviderTests(unittest.TestCase):

    def _provider(self):
        return CoupangLiveBrandProvider({"access_key": "AK", "secret_key": "SK"})

    def test_requires_credentials(self):
        with self.assertRaises(CoupangBrandProviderError):
            CoupangLiveBrandProvider({"access_key": "", "secret_key": ""})

    def test_official_response_shape_is_parsed(self):
        """공식 문서 응답 예시(data.items[].brandId/brandName/
        isUIDRequired/allowedUIDTypes) 그대로를 파싱한다."""

        provider = self._provider()
        provider._request = lambda body: {
            "code": "SUCCESS", "message": "",
            "data": {
                "page": 1, "countPerPage": 10, "totalCount": 1,
                "items": [{
                    "brandId": "KR-5", "brandName": "NIKE",
                    "brandLogoUrl": "https://example.com/logo.png",
                    "isUIDRequired": True,
                    "allowedUIDTypes": ["GTIN", "MPN"],
                }],
            },
        }
        results = provider.search_brand("NIKE")

        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r.brand_id, "KR-5")
        self.assertEqual(r.official_brand_name, "NIKE")
        self.assertTrue(r.is_uid_required)
        self.assertEqual(r.allowed_uid_types, ("GTIN", "MPN"))

    def test_enrollment_status_is_always_unknown_not_guessed(self):
        """공식 응답에는 Enrollment(입점) 상태 필드가 없다 — 모르는
        값을 ENROLLED로 임의 추정하지 않는다."""

        provider = self._provider()
        provider._request = lambda body: {
            "data": {"items": [{"brandId": "KR-5", "brandName": "NIKE"}]},
        }
        results = provider.search_brand("NIKE")

        self.assertEqual(results[0].enrollment_status, "UNKNOWN")

    def test_no_match_returns_empty_list_not_error(self):
        provider = self._provider()
        provider._request = lambda body: {"data": {"items": [], "totalCount": 0}}

        self.assertEqual(provider.search_brand("존재하지않는브랜드"), [])

    def test_request_body_uses_official_field_names(self):
        provider = self._provider()
        captured = {}

        def fake_request(body):
            captured.update(body)
            return {"data": {"items": []}}

        provider._request = fake_request
        provider.search_brand("레이펄스")

        self.assertEqual(captured, {
            "brandName": "레이펄스", "countPerPage": 10, "page": 1,
        })

    def test_items_missing_required_fields_are_skipped_not_fabricated(self):
        provider = self._provider()
        provider._request = lambda body: {
            "data": {"items": [
                {"brandId": "", "brandName": "빈ID브랜드"},
                {"brandId": "KR-9", "brandName": ""},
                {"brandId": "KR-10", "brandName": "정상브랜드"},
            ]},
        }
        results = provider.search_brand("x")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].brand_id, "KR-10")

    def test_real_request_wraps_http_error_without_leaking_body(self):
        import urllib.error
        from unittest.mock import patch

        provider = self._provider()

        def fake_urlopen(*args, **kwargs):
            raise urllib.error.HTTPError(
                "url", 401, "secret-details-should-not-leak", {}, None,
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(CoupangBrandProviderError) as ctx:
                provider.search_brand("x")

        self.assertIn("401", str(ctx.exception))
        self.assertNotIn("secret-details-should-not-leak", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
