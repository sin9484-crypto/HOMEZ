"""
=========================================================
Homez OS

File : app/domains/purchase_task/url_validation.py

작업 B — 후보 상품 URL 검증. javascript:/file:/data: 등 위험
스킴을 거부하고, 등록된 쇼핑몰 코드의 허용 도메인 접미사와 일치하는
https(또는 http) URL만 통과시킨다. DOM 크롤링·자동 접속은 이 모듈
어디에도 없다 — 순수 문자열 검증뿐이다.
=========================================================
"""

from __future__ import annotations

from urllib.parse import urlsplit

from app.domains.purchase_task.constants import ShoppingMallCode

_ALLOWED_SCHEMES = {"http", "https"}


class InvalidCandidateUrlError(ValueError):
    pass


def validate_candidate_url(url: str, shopping_mall_code: str) -> str:
    """검증된 URL을 그대로 반환한다(정규화하지 않음 — 사용자가 붙여넣은
    원문을 그대로 저장해야 실제로 그 페이지로 이동할 수 있다).
    통과 못 하면 InvalidCandidateUrlError."""

    if not url or not url.strip():
        raise InvalidCandidateUrlError("상품 URL을 입력하세요.")

    url = url.strip()
    parts = urlsplit(url)

    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise InvalidCandidateUrlError(
            f"허용되지 않는 URL 스킴입니다: {scheme or '(없음)'} "
            "— http/https만 허용됩니다.",
        )

    host = (parts.hostname or "").lower()
    if not host:
        raise InvalidCandidateUrlError("URL에 호스트가 없습니다.")

    if shopping_mall_code == ShoppingMallCode.OTHER:
        # OTHER는 임의 쇼핑몰 후보를 등록할 수 있게 허용 도메인
        # 검증을 생략한다 — 다만 스킴 검증(위)은 그대로 적용된다.
        return url

    allowed_suffixes = ShoppingMallCode.ALLOWED_DOMAIN_SUFFIXES.get(
        shopping_mall_code,
    )
    if allowed_suffixes is None:
        raise InvalidCandidateUrlError(
            f"알 수 없는 구매처 코드입니다: {shopping_mall_code}",
        )

    if not any(
        host == suffix or host.endswith(f".{suffix}")
        for suffix in allowed_suffixes
    ):
        raise InvalidCandidateUrlError(
            f"{shopping_mall_code} 후보 URL은 "
            f"{'/'.join(allowed_suffixes)} 도메인만 허용됩니다 "
            f"(입력된 호스트: {host}).",
        )

    return url


__all__ = ["InvalidCandidateUrlError", "validate_candidate_url"]
