"""
=========================================================
Homez OS

File : app/domains/product_attribute_match/constants.py

2026-09-15 전면 감사 후속(Phase 9G, 10-4).
=========================================================
"""

from __future__ import annotations


class ProductAttributeField:
    """비교 대상 필드. HOMEZ_USER_OPERATION_SETTINGS.md 10-4가 명시한
    "이름/옵션/수량/사이즈/제조사/원산지" 6개."""

    NAME = "NAME"
    OPTIONS = "OPTIONS"
    QUANTITY = "QUANTITY"
    SIZE = "SIZE"
    MANUFACTURER = "MANUFACTURER"
    ORIGIN_COUNTRY = "ORIGIN_COUNTRY"

    ALL = (NAME, OPTIONS, QUANTITY, SIZE, MANUFACTURER, ORIGIN_COUNTRY)

    LABELS_KO = {
        NAME: "상품명",
        OPTIONS: "옵션",
        QUANTITY: "수량",
        SIZE: "사이즈",
        MANUFACTURER: "제조사",
        ORIGIN_COUNTRY: "원산지",
    }

    # 자동 등록/자동 발주를 막는 판정에 실제로 쓰이는 필드. 전부
    # MATCHED여야 통과한다 — 나머지(현재는 없음)는 정보 표시용으로만
    # 쓰고 자동 진행을 막지는 않는다는 여지를 남겨 둔다.
    REQUIRED_FOR_BLOCKING = ALL


class AttributeMatchStatus:

    MATCHED = "MATCHED"
    MISMATCHED = "MISMATCHED"
    UNCONFIRMED = "UNCONFIRMED"

    ALL = (MATCHED, MISMATCHED, UNCONFIRMED)


class ComparisonRunStatus:

    PASSED = "PASSED"
    BLOCKED = "BLOCKED"

    ALL = (PASSED, BLOCKED)


__all__ = [
    "ProductAttributeField",
    "AttributeMatchStatus",
    "ComparisonRunStatus",
]
