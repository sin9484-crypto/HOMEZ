"""
=========================================================
Homez OS

File : app/domains/media_asset/product_page_extraction.py

Section 1(2026-08-28) — 상품 URL에서 이미지 후보 URL 목록만
추출한다(이미지 바이트 자체는 받지 않는다 — 그건 사용자가 실제로
선택한 뒤 url_import.py::fetch_image_safely()가 담당). 이 파일은
"상품 페이지의 모든 이미지를 무조건 저장하지 않는다"는 요구사항을
그대로 구현한다: 후보 URL만 화면에 보여주고, 실제 다운로드·저장은
사용자 선택 이후에만 일어난다.

CAPTCHA·로그인·보안장치를 우회하지 않는다 — 그런 페이지는 HTML
파싱 결과가 비어 있거나 예상과 다를 뿐이고, 이 파일은 있는 그대로
받아들인다(재시도나 우회 로직 없음).
=========================================================
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.parse import urlparse

from app.domains.media_asset.url_import import Transport
from app.domains.media_asset.url_import import UrlImportError
from app.domains.media_asset.url_import import validate_fetchable_url

MAX_HTML_BYTES = 5 * 1024 * 1024  # 5MB — 상품 페이지 HTML 자체의 상한
MAX_CANDIDATES = 40


@dataclass(frozen=True)
class ImageCandidate:

    url: str
    source_domain: str
    # og:image처럼 "이 페이지의 대표 이미지"로 명시된 경우만 True —
    # 화면이 우선순위를 보여줄 때 참고용.
    is_declared_representative: bool = False


class _ImageTagParser(HTMLParser):
    """표준 라이브러리 html.parser만 사용 — 신뢰할 수 없는 외부
    HTML을 실행 가능한 형태로 다루지 않는다(정규식/DOM 실행 없이
    태그·속성만 읽는다)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.candidates: list[tuple[str, bool]] = []

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        if tag == "img":
            src = attr_dict.get("src")
            if src:
                self.candidates.append((src, False))
            srcset = attr_dict.get("srcset")
            if srcset:
                first = srcset.split(",")[0].strip().split(" ")[0]
                if first:
                    self.candidates.append((first, False))
        elif tag == "meta":
            prop = attr_dict.get("property") or attr_dict.get("name")
            if prop in ("og:image", "twitter:image"):
                content = attr_dict.get("content")
                if content:
                    self.candidates.append((content, True))


_DATA_URL_PREFIX = re.compile(r"^data:", re.IGNORECASE)


def extract_image_candidates(
    html: str, base_url: str,
) -> list[ImageCandidate]:
    """순수 함수 — 네트워크 I/O 없음. 상대경로를 base_url 기준으로
    절대경로화하고, data: URL과 명백히 비-http(s) 스킴은 제외한다.
    실제 접근 가능 여부(SSRF 등)는 여기서 판단하지 않는다 — 사용자가
    실제로 선택해 가져올 때 fetch_image_safely()가 다시 전부
    검증한다(이 함수의 결과를 신뢰해 건너뛰지 않는다)."""

    parser = _ImageTagParser()
    parser.feed(html)

    seen: set[str] = set()
    results: list[ImageCandidate] = []
    for raw_url, is_representative in parser.candidates:
        if _DATA_URL_PREFIX.match(raw_url):
            continue
        absolute = urljoin(base_url, raw_url)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        results.append(ImageCandidate(
            url=absolute, source_domain=parsed.hostname.lower(),
            is_declared_representative=is_representative,
        ))
        if len(results) >= MAX_CANDIDATES:
            break

    # 대표 이미지로 명시된 후보를 목록 앞으로 — 사용자가 먼저 보게.
    results.sort(key=lambda c: not c.is_declared_representative)
    return results


def fetch_product_page_image_candidates(
    product_url: str, transport: Transport, *, timeout: float = 10.0,
) -> list[ImageCandidate]:
    """상품 페이지 HTML을 안전하게 받아 이미지 후보 URL만 추출한다
    (이미지 바이트는 받지 않음). url_import.py와 동일한 SSRF 방어를
    그대로 적용한다."""

    validate_fetchable_url(product_url)

    response = transport.get(
        product_url, timeout=timeout, max_bytes=MAX_HTML_BYTES,
    )
    if response.status_code != 200:
        raise UrlImportError(
            f"상품 페이지를 가져오지 못했습니다(HTTP {response.status_code}).",
        )
    if len(response.body) > MAX_HTML_BYTES:
        raise UrlImportError("상품 페이지가 너무 큽니다.")

    content_type = (response.headers.get("Content-Type") or "").lower()
    if content_type and "html" not in content_type:
        raise UrlImportError(
            f"HTML 페이지가 아닙니다(Content-Type={content_type}).",
        )

    try:
        html = response.body.decode("utf-8", errors="replace")
    except Exception as exc:
        raise UrlImportError("상품 페이지를 해석할 수 없습니다.") from exc

    return extract_image_candidates(html, product_url)


__all__ = [
    "ImageCandidate",
    "extract_image_candidates",
    "fetch_product_page_image_candidates",
]
