"""
=========================================================
Homez OS

File : app/domains/retail_purchase/constants.py

Gate RP-1(2026-08-22 CTO 지시) — 대형 쇼핑몰(네이버·11번가·G마켓·
옥션 등) 구매 실행을 위한 중립 인프라. 소비자 계정 자동 로그인·
화면 자동화 결제는 이 라운드 범위에 없다(사용자 명시 축소) — 실제
`place_order()`는 공식 API 또는 서면 계약된 구매대행 Provider로만
연결한다.
=========================================================
"""

from __future__ import annotations


class RetailPurchaseOrderStatus:
    """
    구매 실행 상태 머신. 소비자 로그인 자동화가 없으므로 LOGIN_REQUIRED/
    AUTH_REQUIRED/PURCHASING 같은 화면 조작 단계는 없다 — QUOTED에서
    바로 PLACING(Provider.place_order 호출 중)으로 간다.
    """

    PROPOSED = "PROPOSED"
    POLICY_CHECKED = "POLICY_CHECKED"
    BUDGET_RESERVED = "BUDGET_RESERVED"
    QUOTED = "QUOTED"
    PLACING = "PLACING"
    ORDERED = "ORDERED"
    TRACKING_PENDING = "TRACKING_PENDING"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REFUND_PENDING = "REFUND_PENDING"
    REFUNDED = "REFUNDED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"

    ALL = (
        PROPOSED, POLICY_CHECKED, BUDGET_RESERVED, QUOTED, PLACING,
        ORDERED, TRACKING_PENDING, SHIPPED, DELIVERED, CANCEL_PENDING,
        CANCELLED, REFUND_PENDING, REFUNDED, BLOCKED, FAILED, UNCERTAIN,
    )

    # 예산이 예약된 상태에서 시작 — 실패/차단/불확실 시 반드시 해제
    # 대상이 되는 상태 집합(FundingHold RELEASED 대상 판단에 사용).
    # UNCERTAIN도 포함한다 — 결과가 불명확한 동안에도 예산은 실제로
    # 점유돼 있고(중복 결제 방지를 위해 해제하지 않음), 동시주문
    # 한도 판정에서도 "진행 중"으로 취급해야 정확하다(2026-08-22
    # 14차 지시 작업 5/6 검증 중 발견 — 예전에는 count_open_orders()
    # 가 UNCERTAIN 건을 누락해 동시주문 한도가 실제보다 낮게 집계될
    # 수 있었다).
    BUDGET_HOLDING = (
        BUDGET_RESERVED, QUOTED, PLACING, ORDERED, TRACKING_PENDING,
        SHIPPED, DELIVERED, CANCEL_PENDING, UNCERTAIN,
    )

    # 결과가 불확실하거나 실패한 상태 — 이 상태에서는 절대 자동
    # 재시도(재결제)하지 않는다. 사람이 쇼핑몰 주문내역에서 실제
    # 존재 여부를 확인한 뒤에만 수동으로 다음 단계를 결정한다.
    NO_AUTO_RETRY = (BLOCKED, FAILED, UNCERTAIN)

    TERMINAL = (CANCELLED, REFUNDED, BLOCKED, FAILED, DELIVERED)


class ProviderCapability:
    """Provider가 실제로 지원하는 기능만 선언한다 — 화면 자동화로
    몰래 보완하지 않는다(지원 안 하면 NOT_SUPPORTED 그대로 표시)."""

    PRODUCT_SEARCH = "PRODUCT_SEARCH"
    LIVE_PRICE = "LIVE_PRICE"
    LIVE_STOCK = "LIVE_STOCK"
    PURCHASE_QUOTE = "PURCHASE_QUOTE"
    ORDER_CREATE = "ORDER_CREATE"
    ORDER_CANCEL = "ORDER_CANCEL"
    TRACKING = "TRACKING"
    REFUND = "REFUND"
    BALANCE = "BALANCE"
    IDEMPOTENCY = "IDEMPOTENCY"
    SANDBOX = "SANDBOX"

    ALL = (
        PRODUCT_SEARCH, LIVE_PRICE, LIVE_STOCK, PURCHASE_QUOTE,
        ORDER_CREATE, ORDER_CANCEL, TRACKING, REFUND, BALANCE,
        IDEMPOTENCY, SANDBOX,
    )


class ProviderConnectionStatus:
    """UI에 표시하는 Provider 연결 상태(지시문 H 섹션 그대로)."""

    TEST_ONLY = "TEST_ONLY"
    CONTRACT_REQUIRED = "CONTRACT_REQUIRED"
    CREDENTIAL_REQUIRED = "CREDENTIAL_REQUIRED"
    CONNECTED = "CONNECTED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    AUTO_PURCHASE_ENABLED = "AUTO_PURCHASE_ENABLED"
    AUTO_PURCHASE_DISABLED = "AUTO_PURCHASE_DISABLED"
    OUTAGE = "OUTAGE"
    INACTIVE = "INACTIVE"

    ALL = (
        TEST_ONLY, CONTRACT_REQUIRED, CREDENTIAL_REQUIRED, CONNECTED,
        REAUTH_REQUIRED, AUTO_PURCHASE_ENABLED, AUTO_PURCHASE_DISABLED,
        OUTAGE, INACTIVE,
    )


class SameProductConfidenceTier:
    """동일상품 판정 신뢰도 등급 — 이미지 유사도·상품명 부분일치만으로
    확정하지 않는다(app/domains/retail_purchase/product_matching.py
    참고)."""

    AUTO_CANDIDATE = "AUTO_CANDIDATE"  # >= 0.98
    NEEDS_REVIEW = "NEEDS_REVIEW"      # 0.90 ~ 0.97
    BLOCKED = "BLOCKED"                # < 0.90 또는 핵심 기준 불일치

    AUTO_CANDIDATE_THRESHOLD = 0.98
    NEEDS_REVIEW_THRESHOLD = 0.90


class RetailPurchasePolicyDecision:
    """구매처 선택정책 판단 결과 — automation_safety.SafetyDecision과
    동일한 3분류 철학(허위로 ALLOW를 만들지 않는다)."""

    ALLOW = "ALLOW"
    REQUIRE_REVIEW = "REQUIRE_REVIEW"
    BLOCK = "BLOCK"


class RetailPurchasePolicyReason:

    PRODUCT_MATCH_INSUFFICIENT = "PRODUCT_MATCH_INSUFFICIENT"
    CORE_ATTRIBUTE_MISMATCH = "CORE_ATTRIBUTE_MISMATCH"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    DELIVERY_DEADLINE_EXCEEDED = "DELIVERY_DEADLINE_EXCEEDED"
    RETURN_NOT_ALLOWED = "RETURN_NOT_ALLOWED"
    SELLER_TRUST_BELOW_MINIMUM = "SELLER_TRUST_BELOW_MINIMUM"
    RETAILER_NOT_ALLOWED = "RETAILER_NOT_ALLOWED"
    MIN_PROFIT_NOT_MET = "MIN_PROFIT_NOT_MET"
    MIN_MARGIN_RATE_NOT_MET = "MIN_MARGIN_RATE_NOT_MET"
    MAX_PURCHASE_PRICE_EXCEEDED = "MAX_PURCHASE_PRICE_EXCEEDED"
    PRICE_INCREASE_RATE_EXCEEDED = "PRICE_INCREASE_RATE_EXCEEDED"
    BUDGET_INSUFFICIENT = "BUDGET_INSUFFICIENT"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    CONCURRENT_ORDER_LIMIT_EXCEEDED = "CONCURRENT_ORDER_LIMIT_EXCEEDED"
    MAX_QUANTITY_PER_PRODUCT_EXCEEDED = "MAX_QUANTITY_PER_PRODUCT_EXCEEDED"
    EMERGENCY_STOP_ACTIVE = "EMERGENCY_STOP_ACTIVE"
    EVIDENCE_REQUIRED = "EVIDENCE_REQUIRED"
    PROVIDER_NOT_CONNECTED = "PROVIDER_NOT_CONNECTED"
    DUPLICATE_SOURCE_ORDER = "DUPLICATE_SOURCE_ORDER"
    POLICY_CHANGED_SINCE_CHECK = "POLICY_CHANGED_SINCE_CHECK"


class ProviderErrorCode:
    """Provider 계약 표준 오류 코드(2026-08-22 14차 지시 — 작업 5).
    각 Provider 구현(Fake·실계약 예정 Provider 전부)은 실패 시 이
    집합 중 하나로 분류해야 한다 — Provider마다 제각각인 자유
    문자열을 그대로 노출하지 않는다(호출자가 재시도 가능 여부·
    사용자 행동을 코드로 분기할 수 있어야 하기 때문)."""

    NOT_SUPPORTED = "NOT_SUPPORTED"
    PROVIDER_NOT_CONNECTED = "PROVIDER_NOT_CONNECTED"
    CREDENTIAL_REQUIRED = "CREDENTIAL_REQUIRED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    PRODUCT_NOT_FOUND = "PRODUCT_NOT_FOUND"
    PRODUCT_MISMATCH = "PRODUCT_MISMATCH"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    PRICE_CHANGED = "PRICE_CHANGED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    RATE_LIMITED = "RATE_LIMITED"
    PURCHASE_FAILED = "PURCHASE_FAILED"
    RESULT_UNCERTAIN = "RESULT_UNCERTAIN"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"

    ALL = (
        NOT_SUPPORTED, PROVIDER_NOT_CONNECTED, CREDENTIAL_REQUIRED,
        REAUTH_REQUIRED, PRODUCT_NOT_FOUND, PRODUCT_MISMATCH, OUT_OF_STOCK,
        PRICE_CHANGED, BUDGET_EXCEEDED, POLICY_BLOCKED, RATE_LIMITED,
        PURCHASE_FAILED, RESULT_UNCERTAIN, PROVIDER_UNAVAILABLE,
    )

    # 재시도(같은 idempotency_key로 place_order 재호출)가 원칙적으로
    # 의미 있는 오류만 모은 집합 — 그래도 실제 재시도 여부는 항상
    # PlaceOrderResult.retryable(Provider가 매 응답마다 명시)을
    # 최종 기준으로 삼는다. 이 집합은 UI가 "재시도 버튼을 보여줄
    # 여지가 있는가"를 판단하는 참고용일 뿐이다.
    POSSIBLY_RETRYABLE = (RATE_LIMITED, PROVIDER_UNAVAILABLE)


class RetailPurchaseUncertainHandlingPolicy:
    """UNCERTAIN 결과 처리정책 — 둘 다 구매를 절대 자동 재시도하지
    않는다(그 원칙은 정책으로 끌 수 없다). 다른 점은 예산 보류
    기간뿐이다."""

    HOLD_INDEFINITELY = "HOLD_INDEFINITELY"
    AUTO_RELEASE_AFTER_TIMEOUT = "AUTO_RELEASE_AFTER_TIMEOUT"

    ALL = (HOLD_INDEFINITELY, AUTO_RELEASE_AFTER_TIMEOUT)


class RetailPurchaseMoneyBasis:
    """순이익 계산에 쓰인 각 항목의 출처 — 허위 수수료·임의 가격을
    만들지 않는다는 원칙을 코드로 강제하기 위한 표시용 상수."""

    CONFIRMED = "CONFIRMED"          # 실제 값(Provider 견적 등)
    ESTIMATED = "ESTIMATED"          # 과거 이력 기반 추정
    UNKNOWN = "UNKNOWN"              # 확인 불가 — 자동구매 차단 사유


__all__ = [
    "RetailPurchaseOrderStatus",
    "ProviderCapability",
    "ProviderConnectionStatus",
    "SameProductConfidenceTier",
    "RetailPurchasePolicyDecision",
    "RetailPurchasePolicyReason",
    "ProviderErrorCode",
    "RetailPurchaseUncertainHandlingPolicy",
    "RetailPurchaseMoneyBasis",
]
