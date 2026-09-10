"""
=========================================================
Homez OS

File : app/domains/automation_safety/constants.py

V2.4 Commerce Safety Layer — 고정 상수
=========================================================
"""


class AutomationMode:

    RECOMMEND_ONLY = "RECOMMEND_ONLY"
    OPERATOR_APPROVAL = "OPERATOR_APPROVAL"
    LIMITED_AUTOMATION = "LIMITED_AUTOMATION"
    DISABLED = "DISABLED"

    ALL = (
        RECOMMEND_ONLY,
        OPERATOR_APPROVAL,
        LIMITED_AUTOMATION,
        DISABLED,
    )

    # 기존 설정이 전혀 없을 때의 안전한 기본값.
    DEFAULT = RECOMMEND_ONLY


class SafetyDecision:

    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"


class SafetyReason:

    EMERGENCY_STOP_ACTIVE = "EMERGENCY_STOP_ACTIVE"
    MODE_NOT_ALLOWED = "MODE_NOT_ALLOWED"
    FUNDING_LIMIT_EXCEEDED = "FUNDING_LIMIT_EXCEEDED"
    QUANTITY_LIMIT_EXCEEDED = "QUANTITY_LIMIT_EXCEEDED"
    OPERATOR_APPROVAL_REQUIRED = "OPERATOR_APPROVAL_REQUIRED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    INVALID_REQUEST = "INVALID_REQUEST"
    # 동일 idempotency_key 재요청 — 신규 실행으로 취급하지 않는다(ALLOW 금지).
    DUPLICATE_REQUEST = "DUPLICATE_REQUEST"


class FunctionCode:
    """
    2026-09-09 Phase 3(HOMEZ_USER_OPERATION_SETTINGS.md 2·3·4·13·14번)
    — "상품 발굴, 상품 등록, 주문 수집, 매입 발주, 결제, 가격 변경,
    재고 대응, 취소, 반품, 환불을 독립적으로 제어한다"에 나열된 10개
    기능을 그대로 코드화한다. 순서도 원문과 동일하게 유지한다(문서와
    코드가 서로 다른 목록을 갖지 않도록).
    """

    PRODUCT_DISCOVERY = "PRODUCT_DISCOVERY"
    PRODUCT_LISTING = "PRODUCT_LISTING"
    ORDER_COLLECTION = "ORDER_COLLECTION"
    PURCHASE_ORDER = "PURCHASE_ORDER"
    PAYMENT = "PAYMENT"
    PRICE_CHANGE = "PRICE_CHANGE"
    INVENTORY_RESPONSE = "INVENTORY_RESPONSE"
    CANCELLATION = "CANCELLATION"
    RETURN = "RETURN"
    REFUND = "REFUND"

    ALL = (
        PRODUCT_DISCOVERY, PRODUCT_LISTING, ORDER_COLLECTION,
        PURCHASE_ORDER, PAYMENT, PRICE_CHANGE, INVENTORY_RESPONSE,
        CANCELLATION, RETURN, REFUND,
    )

    # 화면 표시용 한국어 이름(HOMEZ_USER_OPERATION_SETTINGS.md 원문
    # 그대로) — i18n 카탈로그가 아니라 여기 둔 이유는 이 이름 자체가
    # "무엇을 나열해야 하는가"라는 계약의 일부이기 때문이다(목록이
    # 바뀌면 이 상수와 이름이 항상 함께 바뀌어야 한다).
    LABELS_KO = {
        PRODUCT_DISCOVERY: "상품 발굴",
        PRODUCT_LISTING: "상품 등록",
        ORDER_COLLECTION: "주문 수집",
        PURCHASE_ORDER: "매입 발주",
        PAYMENT: "결제",
        PRICE_CHANGE: "가격 변경",
        INVENTORY_RESPONSE: "재고 대응",
        CANCELLATION: "취소",
        RETURN: "반품",
        REFUND: "환불",
    }


class FunctionMode:
    """
    HOMEZ_USER_OPERATION_SETTINGS.md 14번 — "화면에는 `수동`, `반자동`,
    `자동`, `일시 중지`라는 한국어 상태명과 각 상태가 실제로 하는 일을
    함께 표시한다." 위 4개에 `ERROR`(시스템이 스스로 감지한 오류로
    인한 자동 강등 — 사용자가 직접 선택하는 값이 아니다)를 더해 5개.

    기존 `AutomationMode`(RECOMMEND_ONLY/OPERATOR_APPROVAL/
    LIMITED_AUTOMATION/DISABLED)와는 별개 어휘다 — 그 값은
    `app/domains/automation_safety/service.py::SafetyService`가 이미
    사용 중인 전역 단일 상태(실질적으로 아직 어떤 실행 경로도 이
    `evaluate()`를 호출하지 않는 미배선 상태)이며, 이번에 그 값이나
    호출부를 바꾸지 않는다 — 기존 회귀(tests/test_automation_safety.py,
    49개 검증 지점)를 불필요하게 건드리지 않기 위함이다. 기능별
    자동화는 이 새 어휘(FunctionMode)로 별도 관리한다.
    """

    MANUAL = "MANUAL"
    SEMI_AUTOMATIC = "SEMI_AUTOMATIC"
    AUTOMATIC = "AUTOMATIC"
    PAUSED = "PAUSED"
    ERROR = "ERROR"

    ALL = (MANUAL, SEMI_AUTOMATIC, AUTOMATIC, PAUSED, ERROR)

    # 사용자가 직접 선택해서 "설정"할 수 있는 값(ERROR는 시스템이
    # 스스로 판단해 전이시키는 상태이지, 사용자가 화면에서 고르는
    # 목표 상태가 아니다).
    USER_SELECTABLE = (MANUAL, SEMI_AUTOMATIC, AUTOMATIC, PAUSED)

    # 기존에 설정된 적이 없는 기능의 안전한 기본값 — "자동 모드라는
    # 이유만으로 결제·발주·환불 권한이 확대되지 않는다"는 원칙에 따라
    # 전부 수동으로 시작한다(기능별로 다른 기본값을 예외로 두지
    # 않는다 — 예외 자체가 감사에서 추적하기 어려운 위험이 된다).
    DEFAULT = MANUAL

    LABELS_KO = {
        MANUAL: "수동",
        SEMI_AUTOMATIC: "반자동",
        AUTOMATIC: "자동",
        PAUSED: "일시 중지",
        ERROR: "오류",
    }

    DESCRIPTIONS_KO = {
        MANUAL: "HOMEZ가 준비만 하고, 실행은 항상 사용자가 직접 합니다.",
        SEMI_AUTOMATIC: "HOMEZ가 실행 직전까지 준비하고, 사용자 승인 후 실행합니다.",
        AUTOMATIC: "설정된 한도 안에서 HOMEZ가 자동으로 실행합니다.",
        PAUSED: "이 기능이 일시적으로 멈춰 있습니다. 새 실행을 시작하지 않습니다.",
        ERROR: "오류가 감지되어 자동으로 안전한 상태로 낮아졌습니다. 확인이 필요합니다.",
    }


__all__ = [
    "AutomationMode",
    "SafetyDecision",
    "SafetyReason",
    "FunctionCode",
    "FunctionMode",
]
