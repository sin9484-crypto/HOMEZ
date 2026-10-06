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


class AttributeResolutionContract:
    """해소(resolve_run)·재조회 계약 — 2026-10-05.

    * 해소에 선택값이 필요한 항목은 `REQUIRED_FOR_BLOCKING` 6개뿐이다. 모델명·
      인증정보 같은 비차단 항목이 미확인이어도 그것만으로 해소를 막지 않으며,
      미확인 상태 그대로 남긴다(선택값을 요구하거나 만들어 넣지 않는다).
    * 아래 값들은 "아직 확인하지 못했다"는 뜻의 자리표시자라, 차단 항목의 선택값이
      될 수 없다(근거 없이 해소를 통과시키는 우회). "없음"·"인증대상 아님"처럼
      실제 내용을 말하는 값은 자리표시자가 아니므로 여기에 넣지 않는다.
    * 같은 비교 내용으로 다시 조회된 run은 이전 해소를 이어 쓴다(비교 내용 지문이
      같을 때만, service.find_covering_resolution). 차단 필드가 늘어나 그 해소가
      선택값을 남기지 않은 항목이 생기면 이어 쓰지 않는다.
    """

    VERSION = "1"

    UNCONFIRMED_PLACEHOLDERS = frozenset({
        "미확인", "미제공", "미정", "알수없음", "모름", "확인불가",
        "unknown", "n/a", "na", "none", "null", "tbd", "-", "--", "?",
    })


class SupplierSourceCheck:
    """후보의 `source_reference`(공급처 상품 식별)에 대한 속성 비교 점검 결과."""

    NOT_APPLICABLE = "NOT_APPLICABLE"          # 공급처 상품이 아닌 후보(점검 대상 아님)
    CLEAR = "CLEAR"                            # 식별 가능, 미해소 차단 기록 없음
    BLOCKED = "BLOCKED"                        # 식별 가능, 미해소 BLOCKED 있음
    IDENTIFIER_UNREADABLE = "IDENTIFIER_UNREADABLE"  # 공급처 상품인데 식별자를 읽을 수 없음
    IDENTIFIER_AMBIGUOUS = "IDENTIFIER_AMBIGUOUS"    # 대소문자만 다른 상품 코드에 미해소 차단


class RegistrationBinding:
    """해소된 속성 비교가 **지금 등록하려는 내용**과 이어져 있는지의 점검 결과."""

    NOT_APPLICABLE = "NOT_APPLICABLE"  # 근거로 삼을 해소·통과 기록이 없다(기존 계약: 기록 없음은 통과)
    BOUND = "BOUND"                    # 해소 근거에 기록된 HOMEZ 값과 현재 등록 값이 같다
    UNBOUND = "UNBOUND"                # 다르거나, 해소 근거가 등록 내용을 담고 있지 않다 — 검토 필요


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
    "RegistrationBinding",
    "AttributeResolutionContract",
    "SupplierSourceCheck",
    "ProductAttributeField",
    "AttributeMatchStatus",
    "ComparisonRunStatus",
]
