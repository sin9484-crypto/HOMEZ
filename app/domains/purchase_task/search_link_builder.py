"""
=========================================================
Homez OS

File : app/domains/purchase_task/search_link_builder.py

작업 B — 쇼핑몰별 검색어·검색 URL 생성. 각 쇼핑몰의 공개 검색결과
페이지 URL(사람이 브라우저 주소창에 직접 입력해도 도달하는 일반
검색 URL)에 검색어만 채워 넣는다 — 검색결과를 가져와 파싱하지
않는다(크롤링 아님), 로그인하지 않는다, DOM을 조작하지 않는다.
결과 URL은 HOMEZ 화면에서 새 브라우저 탭으로 여는 용도로만 쓴다.
=========================================================
"""

from __future__ import annotations

from urllib.parse import quote_plus

from app.domains.purchase_task.constants import ShoppingMallCode

_SEARCH_URL_TEMPLATES = {
    ShoppingMallCode.NAVER_SHOPPING: (
        "https://search.shopping.naver.com/search/all?query={query}"
    ),
    ShoppingMallCode.ELEVENST: (
        "https://search.11st.co.kr/Search.tmall?kwd={query}"
    ),
    ShoppingMallCode.GMARKET: (
        "https://browse.gmarket.co.kr/search?keyword={query}"
    ),
    ShoppingMallCode.AUCTION: (
        "https://search.auction.co.kr/search?keyword={query}"
    ),
}


def build_search_keyword(
    *, brand: str | None, product_title: str, model_name: str | None,
    gtin: str | None, capacity: str | None, quantity: int | None,
    color_or_scent: str | None, options: list[str] | None = None,
) -> str:
    """상품 속성을 우선순위대로 이어붙여 검색어를 만든다 — 상품명만
    쓰지 않는다(지시문 필수 확인 문제 1). GTIN이 있으면 가장 신뢰도
    높은 식별자이므로 앞쪽에 둔다."""

    parts: list[str] = []

    if brand:
        parts.append(brand.strip())
    if gtin:
        parts.append(gtin.strip())
    if product_title:
        parts.append(product_title.strip())
    if model_name:
        parts.append(model_name.strip())
    if capacity:
        parts.append(capacity.strip())
    if quantity and quantity > 1:
        parts.append(f"{quantity}개")
    if color_or_scent:
        parts.append(color_or_scent.strip())
    if options:
        parts.extend(o.strip() for o in options if o and o.strip())

    # 중복 제거(순서 유지) — GTIN·브랜드가 상품명에 이미 포함된
    # 경우가 흔하다.
    seen: set[str] = set()
    deduped: list[str] = []
    for p in parts:
        key = p.lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(p)

    return " ".join(deduped).strip()


def build_search_url(shopping_mall_code: str, keyword: str) -> str:

    template = _SEARCH_URL_TEMPLATES.get(shopping_mall_code)
    if template is None:
        raise ValueError(
            f"검색 URL 템플릿이 없는 구매처 코드입니다: "
            f"{shopping_mall_code}",
        )

    return template.format(query=quote_plus(keyword))


def build_search_urls_for_all_malls(keyword: str) -> dict[str, str]:

    return {
        code: build_search_url(code, keyword)
        for code in _SEARCH_URL_TEMPLATES
    }


__all__ = [
    "build_search_keyword",
    "build_search_url",
    "build_search_urls_for_all_malls",
]
