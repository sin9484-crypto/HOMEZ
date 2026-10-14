"""
=========================================================
Homez OS

File : app/domains/product_import/onchannel_page.py

URL 상품 가져오기(2026-10-14) — 온채널 상품 페이지(`/dbcenter_renewal/detail.php?num=…`) 해석.

온채널 페이지는 로그인하지 않으면 가격·공급사가 가려지므로(실제 확인), 이 파일은 **공개로 내려오는 부분**
(상품코드, 제품명, 공급업체분류, 상품정보제공고시 표, 이미지 위치)만 읽는다. 가격·옵션·배송비는 호출부가
온채널 공식 API(상품코드 기준)로 따로 읽는다. 순수 함수(네트워크 없음).

이미지 위치(실제 페이지에서 확인한 구조): 대표·추가 이미지는 `img.prod_img_list`, 상세 설명 이미지는
`div.gosi_wrap` 안의 `figure.image > img`다. 추천 상품 등 다른 영역의 이미지는 담지 않는다.
=========================================================
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field
from urllib.parse import parse_qs
from urllib.parse import urljoin
from urllib.parse import urlparse

from app.domains.product_import.html_tree import Node
from app.domains.product_import.html_tree import label_value_pairs
from app.domains.product_import.html_tree import parse_html

_HOST_SUFFIX = "onch3.co.kr"
_CODE_RE = re.compile(r"\bCH\d{4,}\b")

# 상품정보제공고시 표에서 읽는 라벨(스포츠용품·생활용품 등 공통으로 자주 쓰는 것). 목록 밖 라벨도 notice_rows에는 담는다.
_DESCRIPTION_LABELS = (
    ("제품 구성", "구성"), ("색상", "색상"), ("재질", "소재"), ("크기/중량", "크기"), ("제조국", "제조국"),
)


@dataclass
class OnchannelPage:

    product_code: str | None = None
    title: str | None = None
    supplier_class: str | None = None
    pairs: list[tuple[str, str]] = field(default_factory=list)
    main_image_urls: list[str] = field(default_factory=list)
    detail_image_urls: list[str] = field(default_factory=list)

    def value_of(self, *labels: str) -> str | None:
        for wanted in labels:
            for label, value in self.pairs:
                if label == wanted:
                    return value
        return None


def is_onchannel_product_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if not (host == _HOST_SUFFIX or host.endswith("." + _HOST_SUFFIX)):
        return False
    if not parsed.path.endswith("detail.php"):
        return False
    return bool(re.fullmatch(r"\d{1,12}", (parse_qs(parsed.query).get("num") or [""])[0]))


def _abs(raw: str | None, base_url: str) -> str | None:
    if not raw or raw.lower().startswith("data:"):
        return None
    absolute = urljoin(base_url, raw.strip())
    parsed = urlparse(absolute)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    return absolute


def _is_noise_image(url: str) -> bool:
    lowered = url.lower()
    return any(token in lowered for token in ("no_img", "logo", "footer", "icon", "blank.gif", "spacer"))


def parse_onchannel_page(html: str, page_url: str) -> OnchannelPage:
    root: Node = parse_html(html)
    pairs = label_value_pairs(root)
    page = OnchannelPage(pairs=pairs)

    code_value = None
    for label, value in pairs:
        if label == "상품코드":
            code_value = value
            break
    match = _CODE_RE.search(code_value or "")
    page.product_code = match.group(0) if match else None

    title = None
    for label, value in pairs:
        if label == "제품명":
            title = value
            break
    if not title:
        for node in root.iter_nodes():
            if node.tag == "title":
                title = re.sub(r"\s*-\s*온채널\s*$", "", node.text()) or None
                break
    page.title = title
    for label, value in pairs:
        if label == "공급업체분류":
            page.supplier_class = value
            break

    main: list[str] = []
    detail: list[str] = []
    seen: set[str] = set()
    for node in root.iter_nodes():
        if node.tag != "img":
            continue
        url = _abs(node.attrs.get("src") or node.attrs.get("data-src"), page_url)
        if not url or url in seen or _is_noise_image(url):
            continue
        if "prod_img_list" in node.classes():
            seen.add(url)
            main.append(url)
            continue
        ancestor = node.parent
        while ancestor is not None:
            if "gosi_wrap" in ancestor.classes():
                seen.add(url)
                detail.append(url)
                break
            ancestor = ancestor.parent
    page.main_image_urls = main
    page.detail_image_urls = detail
    return page


def suggest_description(page: OnchannelPage) -> str | None:
    """상품정보제공고시의 사실만 이어 붙인 짧은 설명(효능·광고 표현을 만들지 않는다). 읽은 값이 없으면 None."""

    parts = []
    for label, short in _DESCRIPTION_LABELS:
        value = page.value_of(label)
        if value and value not in ("없음", "해당사항없음", "-"):
            parts.append(f"{short}: {value}")
    return ", ".join(parts) if parts else None
