"""
=========================================================
Homez OS

File : tests/test_media_asset_url_import.py

Section 1(2026-08-28) 이미지 URL 가져오기 SSRF/위장/폭탄 방어 검증.
실제 네트워크 호출은 전혀 하지 않는다 — FakeTransport + fixture
바이트만 사용한다.
=========================================================
"""

import io
import unittest
from unittest.mock import patch

from PIL import Image

from app.domains.media_asset.url_import import FetchedResponse
from app.domains.media_asset.url_import import MAX_DOWNLOAD_BYTES
from app.domains.media_asset.url_import import UrlImportError
from app.domains.media_asset.url_import import fetch_image_safely
from app.domains.media_asset.url_import import validate_fetchable_url


def _real_jpeg_bytes(width=100, height=100) -> bytes:

    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(200, 30, 30)).save(buf, format="JPEG")
    return buf.getvalue()


class FakeTransport:
    """responses: {url: FetchedResponse} — 실제 네트워크 없음."""

    def __init__(self, responses: dict[str, FetchedResponse]):
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url, *, timeout, max_bytes):
        self.calls.append(url)
        if url not in self.responses:
            raise AssertionError(f"FakeTransport에 등록되지 않은 URL 호출: {url}")
        response = self.responses[url]
        if len(response.body) > max_bytes:
            return FetchedResponse(
                status_code=response.status_code,
                headers=response.headers,
                body=response.body[: max_bytes + 1],
                redirect_location=response.redirect_location,
            )
        return response


class ValidateFetchableUrlTestCase(unittest.TestCase):

    def test_rejects_non_http_scheme(self):

        with self.assertRaises(UrlImportError):
            validate_fetchable_url("ftp://example.com/a.jpg")
        with self.assertRaises(UrlImportError):
            validate_fetchable_url("file:///etc/passwd")

    def test_rejects_embedded_credentials(self):

        with self.assertRaises(UrlImportError):
            validate_fetchable_url("https://user:pass@example.com/a.jpg")

    def test_rejects_nonstandard_port(self):

        with self.assertRaises(UrlImportError):
            validate_fetchable_url("https://example.com:8443/a.jpg")

    def test_rejects_localhost_variants(self):

        with self.assertRaises(UrlImportError):
            validate_fetchable_url("http://localhost/a.jpg")
        with self.assertRaises(UrlImportError):
            validate_fetchable_url("http://my-app.local/a.jpg")

    def test_rejects_loopback_and_private_literal_ip(self):

        with self.assertRaises(UrlImportError):
            validate_fetchable_url("http://127.0.0.1/a.jpg")
        with self.assertRaises(UrlImportError):
            validate_fetchable_url("http://10.0.0.5/a.jpg")
        with self.assertRaises(UrlImportError):
            validate_fetchable_url("http://192.168.1.1/a.jpg")

    def test_rejects_link_local(self):

        with self.assertRaises(UrlImportError):
            validate_fetchable_url("http://169.254.169.254/latest/meta-data/")

    def test_allows_public_literal_ip(self):

        validate_fetchable_url("http://8.8.8.8/a.jpg")  # 예외 없어야 함

    def test_dns_rebinding_style_hostname_resolving_to_private_ip_is_blocked(self):
        """호스트명이 실제로는 사설 IP로 풀리는 경우(DNS rebinding
        공격의 전형적 형태)를 차단하는지 — getaddrinfo를 모킹해
        실제 DNS 없이 검증한다."""

        with patch("socket.getaddrinfo") as mock_resolve:
            mock_resolve.return_value = [
                (2, 1, 6, "", ("10.0.0.99", 0)),
            ]
            with self.assertRaises(UrlImportError):
                validate_fetchable_url("http://evil-rebind.example.com/a.jpg")

    def test_hostname_resolving_to_multiple_ips_all_checked(self):
        """DNS가 여러 IP를 반환할 때 하나라도 사설이면 전부 거부 —
        공인 IP 뒤에 사설 IP를 숨기는 라운드로빈 우회 방지."""

        with patch("socket.getaddrinfo") as mock_resolve:
            mock_resolve.return_value = [
                (2, 1, 6, "", ("8.8.8.8", 0)),
                (2, 1, 6, "", ("192.168.0.1", 0)),
            ]
            with self.assertRaises(UrlImportError):
                validate_fetchable_url("http://mixed.example.com/a.jpg")


class FetchImageSafelyTestCase(unittest.TestCase):

    def test_fetches_valid_jpeg(self):

        body = _real_jpeg_bytes(200, 150)
        transport = FakeTransport({
            "http://8.8.8.8/photo.jpg": FetchedResponse(
                status_code=200,
                headers={"Content-Type": "image/jpeg"},
                body=body,
            ),
        })
        result = fetch_image_safely("http://8.8.8.8/photo.jpg", transport)
        self.assertEqual(result.mime_type, "image/jpeg")
        self.assertEqual((result.width, result.height), (200, 150))
        self.assertEqual(result.source_domain, "8.8.8.8")

    def test_html_disguised_as_image_is_rejected(self):
        """Content-Type을 image/jpeg라고 거짓 선언한 HTML 오류
        페이지 — 실제 디코딩에서 실패해야 한다."""

        transport = FakeTransport({
            "http://8.8.8.8/fake.jpg": FetchedResponse(
                status_code=200,
                headers={"Content-Type": "image/jpeg"},
                body=b"<html><body>Access Denied</body></html>",
            ),
        })
        with self.assertRaises(UrlImportError):
            fetch_image_safely("http://8.8.8.8/fake.jpg", transport)

    def test_declared_mime_mismatch_with_actual_is_rejected(self):
        """실제로는 유효한 PNG인데 Content-Type이 image/gif라고
        선언된 경우 — 선언과 실제가 다르면 거부(위장 가능성)."""

        buf = io.BytesIO()
        Image.new("RGB", (50, 50)).save(buf, format="PNG")
        transport = FakeTransport({
            "http://8.8.8.8/mismatch": FetchedResponse(
                status_code=200,
                headers={"Content-Type": "image/gif"},
                body=buf.getvalue(),
            ),
        })
        with self.assertRaises(UrlImportError):
            fetch_image_safely("http://8.8.8.8/mismatch", transport)

    def test_oversized_download_is_rejected(self):

        transport = FakeTransport({
            "http://8.8.8.8/huge.jpg": FetchedResponse(
                status_code=200,
                headers={"Content-Type": "image/jpeg"},
                body=b"x" * (MAX_DOWNLOAD_BYTES + 1000),
            ),
        })
        with self.assertRaises(UrlImportError):
            fetch_image_safely("http://8.8.8.8/huge.jpg", transport)

    def test_pixel_bomb_is_rejected(self):
        """작은 파일 크기(압축률)로도 디코딩하면 거대한 픽셀 수가
        되는 이미지 — 압축폭탄류 방어. 실제로 거대한 이미지를 만들지
        않고, PIL의 크기 계산만 트리거하도록 손실 없는 대형 캔버스를
        만든다."""

        buf = io.BytesIO()
        Image.new("1", (9000, 9000)).save(buf, format="PNG")
        transport = FakeTransport({
            "http://8.8.8.8/bomb.png": FetchedResponse(
                status_code=200,
                headers={"Content-Type": "image/png"},
                body=buf.getvalue(),
            ),
        })
        with self.assertRaises(UrlImportError):
            fetch_image_safely("http://8.8.8.8/bomb.png", transport)

    def test_non_200_status_is_rejected(self):

        transport = FakeTransport({
            "http://8.8.8.8/missing.jpg": FetchedResponse(
                status_code=404, headers={}, body=b"",
            ),
        })
        with self.assertRaises(UrlImportError):
            fetch_image_safely("http://8.8.8.8/missing.jpg", transport)

    def test_redirect_is_followed_and_revalidated(self):

        body = _real_jpeg_bytes()
        transport = FakeTransport({
            "http://8.8.8.8/redirect": FetchedResponse(
                status_code=302, headers={},
                body=b"", redirect_location="http://8.8.4.4/final.jpg",
            ),
            "http://8.8.4.4/final.jpg": FetchedResponse(
                status_code=200,
                headers={"Content-Type": "image/jpeg"},
                body=body,
            ),
        })
        result = fetch_image_safely("http://8.8.8.8/redirect", transport)
        self.assertEqual(result.final_url, "http://8.8.4.4/final.jpg")
        self.assertEqual(transport.calls, [
            "http://8.8.8.8/redirect", "http://8.8.4.4/final.jpg",
        ])

    def test_redirect_to_private_ip_is_blocked(self):
        """리디렉션 목적지가 사설 IP면 재검증 단계에서 차단돼야 한다
        — 최초 URL만 검사하고 리디렉션은 무조건 따라가는 취약점 방지."""

        transport = FakeTransport({
            "http://8.8.8.8/redirect-to-internal": FetchedResponse(
                status_code=302, headers={},
                body=b"", redirect_location="http://169.254.169.254/secret",
            ),
        })
        with self.assertRaises(UrlImportError):
            fetch_image_safely(
                "http://8.8.8.8/redirect-to-internal", transport,
            )

    def test_too_many_redirects_is_rejected(self):

        responses = {}
        for i in range(10):
            responses[f"http://8.8.8.8/hop{i}"] = FetchedResponse(
                status_code=302, headers={}, body=b"",
                redirect_location=f"http://8.8.8.8/hop{i + 1}",
            )
        transport = FakeTransport(responses)
        with self.assertRaises(UrlImportError):
            fetch_image_safely(
                "http://8.8.8.8/hop0", transport, max_redirects=3,
            )


if __name__ == "__main__":
    unittest.main()
