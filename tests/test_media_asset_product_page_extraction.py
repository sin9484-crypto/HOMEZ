"""
=========================================================
Homez OS

File : tests/test_media_asset_product_page_extraction.py

Section 1(2026-08-28) 상품 URL에서 이미지 후보 추출 검증. 실제
외부 사이트 대신 fixture HTML 문자열만 사용한다.
=========================================================
"""

import unittest
from unittest.mock import patch

from app.domains.media_asset.product_page_extraction import (
    extract_image_candidates,
)
from app.domains.media_asset.product_page_extraction import (
    fetch_product_page_image_candidates,
)
from app.domains.media_asset.url_import import FetchedResponse
from app.domains.media_asset.url_import import UrlImportError
from tests.test_media_asset_url_import import FakeTransport

FIXTURE_HTML = """
<html>
<head>
  <meta property="og:image" content="https://cdn.example.com/rep.jpg">
  <meta name="twitter:image" content="/relative-twitter.jpg">
</head>
<body>
  <img src="https://cdn.example.com/gallery/1.jpg">
  <img src="/gallery/2.jpg" alt="second">
  <img src="gallery/3.jpg">
  <img srcset="https://cdn.example.com/gallery/4-small.jpg 480w, https://cdn.example.com/gallery/4-large.jpg 1080w">
  <img src="data:image/png;base64,iVBORw0KGgo=">
  <img src="javascript:alert(1)">
</body>
</html>
"""


class ExtractImageCandidatesTestCase(unittest.TestCase):

    def test_extracts_og_image_and_img_tags(self):

        candidates = extract_image_candidates(
            FIXTURE_HTML, "https://shop.example.com/products/1",
        )
        urls = [c.url for c in candidates]

        self.assertIn("https://cdn.example.com/rep.jpg", urls)
        self.assertIn("https://cdn.example.com/gallery/1.jpg", urls)
        self.assertIn("https://cdn.example.com/gallery/4-small.jpg", urls)

    def test_relative_urls_resolved_against_base(self):

        candidates = extract_image_candidates(
            FIXTURE_HTML, "https://shop.example.com/products/1",
        )
        urls = [c.url for c in candidates]

        self.assertIn("https://shop.example.com/relative-twitter.jpg", urls)
        self.assertIn("https://shop.example.com/gallery/2.jpg", urls)
        self.assertIn(
            "https://shop.example.com/products/gallery/3.jpg", urls,
        )

    def test_data_and_javascript_urls_excluded(self):

        candidates = extract_image_candidates(
            FIXTURE_HTML, "https://shop.example.com/products/1",
        )
        urls = [c.url for c in candidates]

        self.assertFalse(any(u.startswith("data:") for u in urls))
        self.assertFalse(any(u.startswith("javascript:") for u in urls))

    def test_declared_representative_sorted_first(self):

        candidates = extract_image_candidates(
            FIXTURE_HTML, "https://shop.example.com/products/1",
        )
        self.assertTrue(candidates[0].is_declared_representative)
        self.assertEqual(candidates[0].url, "https://cdn.example.com/rep.jpg")

    def test_duplicate_urls_are_deduplicated(self):

        html = (
            '<img src="https://cdn.example.com/x.jpg">'
            '<img src="https://cdn.example.com/x.jpg">'
        )
        candidates = extract_image_candidates(html, "https://shop.example.com/")
        self.assertEqual(len(candidates), 1)

    def test_source_domain_recorded(self):

        candidates = extract_image_candidates(
            FIXTURE_HTML, "https://shop.example.com/products/1",
        )
        rep = next(c for c in candidates if c.url == "https://cdn.example.com/rep.jpg")
        self.assertEqual(rep.source_domain, "cdn.example.com")


def _fake_public_dns(*_args, **_kwargs):
    return [(2, 1, 6, "", ("93.184.216.34", 0))]


class FetchProductPageImageCandidatesTestCase(unittest.TestCase):
    """fixture 도메인이 실제로 존재하지 않으므로(진짜 네트워크를 쓰지
    않는다는 요구사항 그대로) DNS 해석 결과 자체도 모킹한다 — 공인
    IP로 풀린다고 가정하고 SSRF 검증 로직 자체만 통과시킨다."""

    def test_fetches_and_extracts(self):

        transport = FakeTransport({
            "https://shop.example.com/products/1": FetchedResponse(
                status_code=200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                body=FIXTURE_HTML.encode("utf-8"),
            ),
        })
        with patch("socket.getaddrinfo", side_effect=_fake_public_dns):
            candidates = fetch_product_page_image_candidates(
                "https://shop.example.com/products/1", transport,
            )
        self.assertGreater(len(candidates), 0)

    def test_non_html_content_type_is_rejected(self):

        transport = FakeTransport({
            "https://shop.example.com/products/1": FetchedResponse(
                status_code=200,
                headers={"Content-Type": "application/pdf"},
                body=b"%PDF-1.4",
            ),
        })
        with patch("socket.getaddrinfo", side_effect=_fake_public_dns):
            with self.assertRaises(UrlImportError):
                fetch_product_page_image_candidates(
                    "https://shop.example.com/products/1", transport,
                )

    def test_private_host_blocked_before_fetch(self):

        transport = FakeTransport({})
        with self.assertRaises(UrlImportError):
            fetch_product_page_image_candidates(
                "http://192.168.0.1/products/1", transport,
            )
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
