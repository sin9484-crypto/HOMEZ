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


__all__ = [
    "AutomationMode",
    "SafetyDecision",
    "SafetyReason",
]
