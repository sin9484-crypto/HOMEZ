"""
=========================================================
Homez OS

File : app/domains/product_import/generic_extractor.py

URL 상품 가져오기(2026-10-14) — 일반 상품 페이지에서 제안값을 best-effort로 읽는다.

JSON-LD(Product) → OpenGraph/메타 → <title> 순으로 상품명·설명·가격 문구·브랜드 힌트를 찾고, 표에서
라벨/값 쌍을 모은다. 사이트마다 구조가 달라 못 읽는 값이 많을 수 있고, 그 값은 비워 둔다(추측 금지).
로그인·CAPTCHA를 우회하지 않는다 — 공개로 내려온 HTML만 읽는다. 순수 함수(네트워크 없음).
=========================================================
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from dataclasses import field
from urllib.parse import urljoin
from urllib.parse import urlparse

from app.domains.product_import.html_tree import Node
from app.domains.product_import.html_tree import label_value_pairs
from app.domains.product_import.html_tree import parse_html

# 쿠팡이 의료기기·효능 표현으로 오인할 수 있는 단어 — 상품명에 있으면 경고만 한다(자동으로 지우지 않는다).
CLAIM_WORDS = (
    "치료", "예방", "완화", "개선", "재생", "연골", "관절염", "통증", "디스크", "교정", "효능", "의료", "질환",
)

_PRICE_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{3,})\s*원")


@dataclass
class ExtractedImage:

    url: str
    css_classes: tuple[str, ...] = ()
    declared_representative: bool = False


@dataclass
class ExtractedPage:

    title: str | None = None
    description: str | None = None
    price_text: str | None = None
    brand_hint: str | None = None
    pairs: list[tuple[str, str]] = field(default_factory=list)
    images: list[ExtractedImage] = field(default_factory=list)
    root: Node | None = None


def claim_words_in(name: str | None) -> list[str]:
    return [w for w in CLAIM_WORDS if name and w in name]


def _meta(root: Node) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in root.iter_nodes():
        if node.tag == "meta":
            key = node.attrs.get("property") or node.attrs.get("name")
            content = node.attrs.get("content")
            if key and content and key.lower() not in out:
                out[key.lower()] = content.strip()
    return out


def _json_ld_products(root: Node) -> list[dict]:
    products: list[dict] = []
    for node in root.iter_nodes():
        if node.tag == "script" and (node.attrs.get("type") or "").lower() == "application/ld+json":
            raw = "".join(c for c in node.children if isinstance(c, str)).strip()
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            stack = [data]
            while stack:
                item = stack.pop()
                if isinstance(item, list):
                    stack.extend(item)
                elif isinstance(item, dict):
                    kind = item.get("@type")
                    kinds = kind if isinstance(kind, list) else [kind]
                    if "Product" in kinds:
                        products.append(item)
                    graph = item.get("@graph")
                    if isinstance(graph, list):
                        stack.extend(graph)
    return products


def _offer_price(product: dict) -> str | None:
    offers = product.get("offers")
    candidates = offers if isinstance(offers, list) else [offers]
    for offer in candidates:
        if isinstance(offer, dict):
            price = offer.get("price") or offer.get("lowPrice")
            if price not in (None, ""):
                currency = offer.get("priceCurrency") or ""
                return f"{price} {currency}".strip()
    return None


def _image_sources(root: Node, base_url: str) -> list[ExtractedImage]:
    seen: set[str] = set()
    images: list[ExtractedImage] = []

    def add(raw: str | None, classes: tuple[str, ...], representative: bool) -> None:
        if not raw or raw.lower().startswith("data:"):
            return
        absolute = urljoin(base_url, raw.strip())
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or absolute in seen:
            return
        seen.add(absolute)
        images.append(ExtractedImage(absolute, classes, representative))

    meta = _meta(root)
    add(meta.get("og:image"), (), True)
    for node in root.iter_nodes():
        if node.tag == "img":
            src = node.attrs.get("src") or node.attrs.get("data-src") or node.attrs.get("data-original")
            add(src, tuple(sorted(node.classes())), False)
    return images


def extract_generic(html: str, page_url: str) -> ExtractedPage:
    root = parse_html(html)
    meta = _meta(root)
    products = _json_ld_products(root)

    title = None
    description = None
    price_text = None
    brand_hint = None
    if products:
        first = products[0]
        title = (first.get("name") or "").strip() or None
        description = (first.get("description") or "").strip() or None
        price_text = _offer_price(first)
        brand = first.get("brand")
        if isinstance(brand, dict):
            brand = brand.get("name")
        brand_hint = brand.strip() if isinstance(brand, str) and brand.strip() else None

    title = title or meta.get("og:title") or meta.get("twitter:title")
    if not title:
        for node in root.iter_nodes():
            if node.tag == "title":
                title = node.text() or None
                break
    description = description or meta.get("og:description") or meta.get("description")
    price_text = price_text or meta.get("product:price:amount") or meta.get("og:price:amount")
    if not price_text:
        found = _PRICE_RE.search(root.text()[:200_000])
        price_text = found.group(0) if found else None

    return ExtractedPage(
        title=(title or "").strip() or None,
        description=(description or "").strip() or None,
        price_text=price_text,
        brand_hint=brand_hint,
        pairs=label_value_pairs(root),
        images=_image_sources(root, page_url),
        root=root,
    )
