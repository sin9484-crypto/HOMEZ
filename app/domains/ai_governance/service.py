"""
=========================================================
Homez OS

File : app/domains/ai_governance/service.py

AI Capability Registry — 조회·강제 헬퍼. 이 서비스는 "이 capability_
code가 실제로 등록되고 활성 상태인가"만 판단한다. 실제 실행 허용
여부(execution_allowed)는 절대 이 서비스가 결정하지 않는다 — 항상
각 도메인의 SafetyService(EStop/AutomationMode)·Permission Guard·
ApprovalService·ChannelPolicyService가 최종 결정한다. 이 서비스가
하는 일은 오직 "역할 계약이 없거나 비활성인 호출을 차단"하는 것
뿐이다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from app.core.exceptions import ForbiddenException
from app.domains.ai_governance.capability_catalog import AI_CAPABILITY_CATALOG
from app.domains.ai_governance.capability_catalog import CAPABILITY_CATALOG_VERSION
from app.domains.ai_governance.capability_catalog import CapabilityContract
from app.domains.ai_governance.schema import AIResultEnvelope

_CATALOG_BY_CODE: dict[str, CapabilityContract] = {
    c.capability_code: c for c in AI_CAPABILITY_CATALOG
}


class UnknownCapabilityError(ForbiddenException):

    def __init__(self, capability_code: str):

        super().__init__(
            f"AI_CAPABILITY_NOT_REGISTERED: '{capability_code}'는 AI "
            "Capability Registry에 등록되지 않은 기능입니다 — 역할 계약이 "
            "없는 호출은 차단됩니다.",
        )


class InactiveCapabilityError(ForbiddenException):

    def __init__(self, capability_code: str):

        super().__init__(
            f"AI_CAPABILITY_INACTIVE: '{capability_code}'는 등록돼 있지만 "
            "현재 비활성(active=False) 상태입니다.",
        )


def get_capability(capability_code: str) -> CapabilityContract | None:

    return _CATALOG_BY_CODE.get(capability_code)


def require_active_capability(capability_code: str) -> CapabilityContract:
    """
    등록되지 않았거나 비활성인 capability_code는 즉시 예외를 던진다
    (fail-closed). 호출자는 이 함수가 반환한 계약의 `max_automation_
    level`/`required_permission`/`execution_limit`을 참고할 수 있지만,
    이 함수 자체는 실행을 승인하지 않는다 — 그 판단은 여전히 호출자
    도메인의 기존 게이트(Permission/EStop/Approval/ChannelPolicy)가
    전담한다.
    """

    contract = _CATALOG_BY_CODE.get(capability_code)
    if contract is None:
        raise UnknownCapabilityError(capability_code)
    if not contract.active:
        raise InactiveCapabilityError(capability_code)

    return contract


def list_capabilities() -> list[CapabilityContract]:

    return list(AI_CAPABILITY_CATALOG)


def build_ai_result_envelope(
    *,
    capability_code: str,
    result_type: str,
    decision: str | None = None,
    confirmed_facts: dict | None = None,
    calculated_values: dict | None = None,
    assumptions: list[str] | None = None,
    missing_evidence: list[str] | None = None,
    blocking_rules: list[str] | None = None,
    confidence: float | None = None,
    recommended_actions: list[str] | None = None,
    execution_allowed: bool = False,
    policy_version: str | None = None,
    evaluated_at: datetime | None = None,
) -> AIResultEnvelope:
    """
    Audit(2026-08-21, CTO 후속 지시) — 공통 AI 결과 계약(schema.py::
    AIResultEnvelope)을 일관되게 조립하는 유일한 진입점. 이 함수는
    `require_active_capability()`를 다시 호출하지 않는다 — 호출자가
    이미 자신의 실행 지점에서 그 검사를 통과한 뒤에만 결과를 조립
    하도록 기대한다(이중 호출 방지). `execution_allowed`는 언제나
    호출자가 자신의 실제 게이트 결과를 그대로 전달해야 한다 — 이
    함수 자신은 절대 True/False를 스스로 판단하지 않는다(기본값
    False가 fail-closed 기본값).
    """

    contract = get_capability(capability_code)
    if contract is None:
        raise UnknownCapabilityError(capability_code)

    return AIResultEnvelope(
        result_type=result_type,
        decision=decision,
        confirmed_facts=confirmed_facts or {},
        calculated_values=calculated_values or {},
        assumptions=assumptions or [],
        missing_evidence=missing_evidence or [],
        blocking_rules=blocking_rules or [],
        confidence=confidence,
        recommended_actions=recommended_actions or [],
        execution_allowed=execution_allowed,
        capability_code=capability_code,
        capability_version=CAPABILITY_CATALOG_VERSION,
        policy_version=policy_version,
        evaluated_at=evaluated_at or datetime.utcnow(),
    )


__all__ = [
    "CAPABILITY_CATALOG_VERSION",
    "UnknownCapabilityError",
    "InactiveCapabilityError",
    "get_capability",
    "require_active_capability",
    "list_capabilities",
    "build_ai_result_envelope",
]
