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
    "이름/옵션/수량/사이즈/제조사/원산지" 6개 + 2026-09-16 전면 감사
    후속(Adapter 계약 확장)이 추가한 모델명·인증정보 2개."""

    NAME = "NAME"
    OPTIONS = "OPTIONS"
    QUANTITY = "QUANTITY"
    SIZE = "SIZE"
    MANUFACTURER = "MANUFACTURER"
    ORIGIN_COUNTRY = "ORIGIN_COUNTRY"
    MODEL_NAME = "MODEL_NAME"
    CERTIFICATION_IDENTIFIERS = "CERTIFICATION_IDENTIFIERS"

    ALL = (
        NAME, OPTIONS, QUANTITY, SIZE, MANUFACTURER, ORIGIN_COUNTRY,
        MODEL_NAME, CERTIFICATION_IDENTIFIERS,
    )

    LABELS_KO = {
        NAME: "상품명",
        OPTIONS: "옵션",
        QUANTITY: "수량",
        SIZE: "사이즈",
        MANUFACTURER: "제조사",
        ORIGIN_COUNTRY: "원산지",
        MODEL_NAME: "모델명",
        CERTIFICATION_IDENTIFIERS: "인증정보",
    }

    # 자동 등록/자동 발주를 막는 판정에 실제로 쓰이는 필드 — 원문
    # 10-4가 명시한 6개(이름/옵션/수량/사이즈/제조사/원산지)만
    # 그대로 유지한다. 모델명·인증정보는 비교·표시는 하되(ALL에는
    # 포함), 그 자체가 자동 진행을 막지는 않는다 — 온채널 등
    # 공급처 쪽에서 아직 공식적으로 노출하지 않는 필드라 "필수"로
    # 강제하면 사실상 모든 상품이 영구 차단되는 부작용이 크다.
    REQUIRED_FOR_BLOCKING = (
        NAME, OPTIONS, QUANTITY, SIZE, MANUFACTURER, ORIGIN_COUNTRY,
    )


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
