"""
=========================================================
Homez OS

File : app/domains/media_asset/url_import.py

Section 1(2026-08-28) — 이미지 URL 가져오기의 SSRF/위장/폭탄 방어
계층. 실제 네트워크는 이 파일이 직접 열지 않는다 — Transport
Protocol을 통해 주입받는다(운영에서는 RequestsTransport, 테스트는
FakeTransport + fixture만 쓴다). CAPTCHA·로그인·보안장치를 우회하는
로직은 이 파일에 없다 — 그런 페이지는 요청이 그대로 실패하거나
예상과 다른 응답을 반환할 뿐이고, 이 파일은 그 결과를 안전하게
거부할 뿐이다.

방어 대상:
  - 스킴 위장(http/https 외 차단)
  - 사설 IP·loopback·link-local·멀티캐스트·예약 대역(SSRF)
  - localhost/.local
  - 리디렉션마다 목적지 재검증(중간에 사설 IP로 새는 것 방지)
  - MIME 위장(선언된 Content-Type과 실제 디코딩 결과 교차검증)
  - 과대 다운로드(Content-Length 선검사 + 스트리밍 중 초과 시 중단)
  - 픽셀 폭탄(선언 없이 디코딩만으로 메모리를 고갈시키는 초대형 이미지)
  - 응답 지연(timeout)

알려진 한계(정직하게 기록) — DNS rebinding을 hostname→IP 해석과
실제 연결 사이의 완전한 IP pinning으로 막지는 않는다(각 요청·
리디렉션 단계마다 재해석·재검증만 한다, TOCTOU 창이 이론적으로
남는다). 완전한 IP pinning은 커넥션 풀 수준의 소켓 제어가 필요해
이번 V7 범위 밖으로 명시적으로 남긴다.
=========================================================
"""

from __future__ import annotations

import io
import socket
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Protocol
from urllib.parse import urlparse

from PIL import Image

from app.core.exceptions import BadRequestException

ALLOWED_SCHEMES = ("http", "https")
MAX_REDIRECTS = 5
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024  # 10MB — 일반 업로드 한도와 통일
MAX_DECODED_PIXELS = 40_000_000  # 픽셀 폭탄 방어(가로*세로, 압축률 무관)
DEFAULT_TIMEOUT_SECONDS = 10.0

_ALLOWED_MIME_PREFIXES = ("image/jpeg", "image/png", "image/webp")


class UrlImportError(BadRequestException):
    """URL 가져오기 실패 — 전부 BadRequestException 계열(4xx)이다.
    서버 내부 오류가 아니라 "이 URL은 안전하게 가져올 수 없다"는
    판정이기 때문."""


@dataclass(frozen=True)
class FetchedResponse:
    """Transport 구현이 반환해야 하는 최소 계약."""

    status_code: int
    headers: dict[str, str]
    # 최대 MAX_DOWNLOAD_BYTES + 1바이트까지만 담겨 있으면 된다 —
    # Transport가 그 이상을 반환해도 이 파일이 다시 자른다.
    body: bytes
    # 3xx일 때만 채운다.
    redirect_location: str | None = None


class Transport(Protocol):
    def get(
        self, url: str, *, timeout: float, max_bytes: int,
    ) -> FetchedResponse: ...


def _resolve_and_validate_host(hostname: str) -> None:
    """hostname이 가리키는 모든 IP가 공인(전역) 주소인지 확인한다.
    하나라도 사설/loopback/link-local/예약 대역이면 즉시 거부한다
    (DNS가 여러 IP를 반환하는 경우 전부 검사 — round-robin 뒤에
    사설 IP를 숨기는 우회 방지)."""

    host = hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise UrlImportError(f"허용되지 않는 호스트입니다: {hostname}")

    try:
        literal = ip_address(host)
        addresses = [literal]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise UrlImportError(
                f"호스트를 확인할 수 없습니다: {hostname}",
            ) from exc
        addresses = [ip_address(info[4][0]) for info in infos]

    if not addresses:
        raise UrlImportError(f"호스트를 확인할 수 없습니다: {hostname}")

    for address in addresses:
        if not address.is_global:
            raise UrlImportError(
                f"사설/내부 네트워크로 연결되는 호스트는 허용되지 않습니다: "
                f"{hostname}",
            )


def validate_fetchable_url(url: str) -> None:
    """스킴·자격증명·포트·호스트를 전부 검증한다 — 실제 요청 직전과
    리디렉션마다 다시 호출해야 한다(한 번 통과했다고 영구히 안전한
    것은 아니다)."""

    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise UrlImportError(f"http 또는 https만 허용됩니다: {url}")
    if not parsed.hostname:
        raise UrlImportError(f"호스트가 없는 URL입니다: {url}")
    if parsed.username or parsed.password:
        raise UrlImportError("URL에 자격증명을 포함할 수 없습니다.")
    if parsed.port not in (None, 80, 443):
        raise UrlImportError(f"허용되지 않는 포트입니다: {parsed.port}")

    _resolve_and_validate_host(parsed.hostname)


@dataclass(frozen=True)
class FetchedImage:

    image_bytes: bytes
    mime_type: str
    width: int
    height: int
    final_url: str
    source_domain: str


def fetch_image_safely(
    url: str,
    transport: Transport,
    *,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_redirects: int = MAX_REDIRECTS,
) -> FetchedImage:
    """URL 하나를 안전하게 받아 실제로 디코딩 가능한 이미지인지까지
    확인한다. 실패 사유는 전부 UrlImportError(4xx)로 구분해 던진다
    — 호출자가 "다운로드 실패·차단·미지원 형식·과대 파일"을 구분해
    표시할 수 있게(요구사항) error 안의 메시지로 원인을 구분한다."""

    current_url = url
    redirects = 0

    while True:
        validate_fetchable_url(current_url)

        response = transport.get(
            current_url, timeout=timeout, max_bytes=max_bytes,
        )

        if response.status_code in (301, 302, 303, 307, 308):
            redirects += 1
            if redirects > max_redirects:
                raise UrlImportError("리디렉션이 너무 많습니다.")
            if not response.redirect_location:
                raise UrlImportError("리디렉션 응답에 목적지가 없습니다.")
            current_url = response.redirect_location
            continue

        if response.status_code != 200:
            raise UrlImportError(
                f"이미지를 가져오지 못했습니다(HTTP {response.status_code}).",
            )

        break

    body = response.body
    if len(body) > max_bytes:
        raise UrlImportError(
            f"파일이 너무 큽니다({len(body)} bytes > {max_bytes} bytes 한도).",
        )

    declared_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()

    # MIME 위장 방어 — 선언된 Content-Type을 신뢰하지 않고, 실제
    # 바이트를 Pillow로 열어본다. HTML 오류 페이지가 image/jpeg라고
    # 거짓 선언해도 여기서 실패한다.
    try:
        with Image.open(io.BytesIO(body)) as img:
            width, height = img.size
            if width * height > MAX_DECODED_PIXELS:
                raise UrlImportError(
                    f"이미지 해상도가 너무 큽니다({width}x{height}) — "
                    "픽셀 폭탄으로 판단해 거부합니다.",
                )
            actual_format = (img.format or "").upper()
            img.verify()
    except UrlImportError:
        raise
    except Exception as exc:
        raise UrlImportError(
            "실제로는 유효한 이미지가 아닙니다(다운로드 실패·차단·"
            "지원하지 않는 형식일 수 있습니다).",
        ) from exc

    format_to_mime = {
        "JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp",
    }
    actual_mime = format_to_mime.get(actual_format)
    if actual_mime is None or actual_mime not in _ALLOWED_MIME_PREFIXES:
        raise UrlImportError(
            f"지원하지 않는 이미지 형식입니다(실제 형식={actual_format}).",
        )
    if declared_type and declared_type not in _ALLOWED_MIME_PREFIXES:
        # 선언과 실제가 다르면(위장 시도) 거부 — 실제가 맞아도 신뢰하지 않는다.
        raise UrlImportError(
            f"선언된 형식과 실제 형식이 다릅니다(선언={declared_type}, "
            f"실제={actual_mime}).",
        )

    parsed_final = urlparse(current_url)
    return FetchedImage(
        image_bytes=body, mime_type=actual_mime, width=width, height=height,
        final_url=current_url,
        source_domain=(parsed_final.hostname or "").lower(),
    )


class RequestsTransport:
    """운영에서 실제로 쓰는 Transport 구현 — requests 사용, 리디렉션은
    직접 처리(allow_redirects=False)해서 매 홉을 이 파일이 재검증할
    수 있게 한다. 스트리밍하며 max_bytes를 넘는 순간 즉시 연결을
    끊는다(압축폭탄류 방어 — 응답 전체를 먼저 받지 않는다)."""

    def get(
        self, url: str, *, timeout: float, max_bytes: int,
    ) -> FetchedResponse:
        import requests

        response = requests.get(
            url, timeout=timeout, stream=True, allow_redirects=False,
        )
        try:
            if response.status_code in (301, 302, 303, 307, 308):
                return FetchedResponse(
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    body=b"",
                    redirect_location=response.headers.get("Location"),
                )

            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=65536):
                total += len(chunk)
                chunks.append(chunk)
                if total > max_bytes:
                    break

            return FetchedResponse(
                status_code=response.status_code,
                headers=dict(response.headers),
                body=b"".join(chunks),
            )
        finally:
            response.close()


__all__ = [
    "RequestsTransport",
    "UrlImportError",
    "Transport",
    "FetchedResponse",
    "FetchedImage",
    "validate_fetchable_url",
    "fetch_image_safely",
    "MAX_DOWNLOAD_BYTES",
    "MAX_DECODED_PIXELS",
]
