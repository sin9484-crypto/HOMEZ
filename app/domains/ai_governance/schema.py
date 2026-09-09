"""
=========================================================
Homez OS

File : app/domains/ai_governance/schema.py

AI Capability Registry — API 경계 Pydantic Schema.
=========================================================
"""

from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class CapabilityContractResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    capability_code: str
    display_name: str
    capability_type: str
    purpose: str
    allowed_operations: tuple[str, ...]
    forbidden_operations: tuple[str, ...]
    required_inputs: tuple[str, ...]
    allowed_basis: str
    decision_criteria: str
    missing_data_handling: str
    required_permission: str | None
    max_automation_level: str
    user_approval_condition: str
    execution_limit: str | None
    stop_condition: str
    external_provider: str | None
    model_prompt_policy_version: str
    audit_fields: tuple[str, ...]
    active: bool
    implementation_reference: str

    # AG-2(2026-08-21) 신규 필드 — API 응답에도 그대로 노출한다.
    display_name_en: str = ""
    responsibilities: tuple[str, ...] = ()
    allowed_evidence_sources: tuple[str, ...] = ()
    output_contract: tuple[str, ...] = ()
    actual_entry_points: tuple[str, ...] = ()
    provider_status: str = ""
    unimplemented_dependencies: tuple[str, ...] = ()


class AIInvocationContext(BaseModel):
    """
    AG-1(2026-08-21) — AI 판단 경로 호출 시점의 요청 맥락을 구조화
    한다. 이 객체 자체는 아무것도 판단하거나 승인하지 않는다 — 호출을
    감사·추적하기 위한 순수 데이터 묶음이다. 사람이 직접 수행하는
    CRUD 경로(execution_origin=HUMAN)에는 이 객체가 필요하지 않다 —
    기존 Router의 current_user/company_id만으로 충분하다(AG-0 원칙:
    AI Capability Registry가 일반 CRUD의 게이트가 되지 않는다).
    """

    capability_code: str
    capability_version: str
    company_id: int
    user_id: int | None = None
    execution_origin: str
    correlation_id: str
    input_fingerprint: str | None = None
    policy_version: str | None = None
    requested_at: datetime


class ProposedAction(BaseModel):
    """
    AG-4(2026-08-21) — AI가 실행이 필요한 제안을 만들 때 쓰는 공통
    계약. AI는 이 객체를 DRAFT/REVIEW_REQUIRED 상태로만 생성한다 —
    APPROVED/EXECUTED 전이는 사람의 명시적 승인(또는 그 승인을
    반영하는 서버 로직)만 수행한다(app/domains/ai_governance/
    proposed_action_service.py 참고).
    """

    id: int | None = None
    company_id: int
    capability_code: str
    action_type: str
    target_entity: str
    proposed_payload: dict
    reason: str
    evidence: list[str] = Field(default_factory=list)
    risk_level: str
    approval_required: bool = True
    input_fingerprint: str
    status: str
    created_by: int | None = None
    decided_by: int | None = None
    expires_at: datetime | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ProposedActionResponse(BaseModel):
    """
    Gate AI-F(2026-08-22) — ProposedAction DB 행의 전체 상세 응답
    (ORM → JSON 변환용, `ProposedAction`(위, 요청/생성용 순수 데이터
    계약)과는 별개 — 이쪽은 id/decided_by/executed_at 등 서버가
    채우는 필드까지 전부 노출한다). `model_validate(action)`을 직접
    쓰지 않는다 — ORM 컬럼명(proposed_payload_json/evidence_json)과
    이 응답 필드명(proposed_payload/evidence)이 달라 `from_attributes`
    자동 매핑이 적용되지 않는다.
    """

    id: int
    company_id: int
    capability_code: str
    action_type: str
    target_entity: str
    proposed_payload: dict
    reason: str
    evidence: list[str]
    risk_level: str
    approval_required: bool
    input_fingerprint: str
    status: str
    created_by: int | None
    decided_by: int | None
    decided_at: datetime | None
    decision_reason: str | None
    executed_at: datetime | None
    executed_reference: str | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, action) -> "ProposedActionResponse":

        import json

        return cls(
            id=action.id,
            company_id=action.company_id,
            capability_code=action.capability_code,
            action_type=action.action_type,
            target_entity=action.target_entity,
            proposed_payload=json.loads(action.proposed_payload_json),
            reason=action.reason,
            evidence=json.loads(action.evidence_json),
            risk_level=action.risk_level,
            approval_required=action.approval_required,
            input_fingerprint=action.input_fingerprint,
            status=action.status,
            created_by=action.created_by,
            decided_by=action.decided_by,
            decided_at=action.decided_at,
            decision_reason=action.decision_reason,
            executed_at=action.executed_at,
            executed_reference=action.executed_reference,
            expires_at=action.expires_at,
            created_at=action.created_at,
            updated_at=action.updated_at,
        )


class ApproveProposedActionRequest(BaseModel):
    """
    recent_auth_token은 여기 없다 — 이 코드베이스의 기존 관례
    (app/domains/company/router.py, app/core/migration_approval.py)와
    동일하게 `X-Recent-Auth-Token` HTTP 헤더로만 전달한다(요청 바디에
    두지 않음, Router의 Header(alias=...) 의존성이 직접 읽는다).
    """

    current_payload_for_refingerprint: dict | None = None
    decision_reason: str | None = None


class RejectProposedActionRequest(BaseModel):

    reason: str = Field(..., min_length=1, max_length=1000)


class AIResultEnvelope(BaseModel):
    """
    Audit(2026-08-21, CTO 후속 지시) — 모든 AI/규칙/계산 결과가 공유
    하는 공통 결과 계약. 완성된 한국어 문장이 아니라 구조화된 값만
    담는다(listing_wizard_precheck.py/channel_policy/schema.py와 동일
    원칙 — 화면 i18n이 최종 문구를 조립한다).

    `execution_allowed`는 이 봉투 자신이 계산하지 않는다 — 언제나
    호출한 도메인의 실제 게이트(Permission/EStop/Approval/
    ChannelPolicy) 결과를 그대로 반영만 한다. 이 필드가 True라고
    해서 실행이 저절로 일어나지 않는다 — 실행은 항상 별도의 명시적
    Service 호출(승인/제출/발주 등)이 수행한다.

    기존 API 응답 스키마(ChannelPolicyEvaluationResponse 등)는 이
    봉투로 교체하지 않는다 — 대규모 호환성 파괴를 피하기 위해 필요한
    곳에 선택적 필드/별도 조회로 단계적으로 얹는다
    (`build_ai_result_envelope()` 참고).
    """

    result_type: str
    decision: str | None = None
    confirmed_facts: dict = Field(default_factory=dict)
    calculated_values: dict = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    blocking_rules: list[str] = Field(default_factory=list)
    confidence: float | None = None
    recommended_actions: list[str] = Field(default_factory=list)
    execution_allowed: bool = False
    capability_code: str
    capability_version: str
    policy_version: str | None = None
    evaluated_at: datetime

    # 하위 호환 — 이전 버전 필드(내부 어디에서도 아직 참조하지 않지만,
    # 향후 얇은 adapter가 과거 응답 모양을 흉내내야 할 때를 대비해
    # 유지). 신규 코드는 위 필드만 사용한다.
    data: dict = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    requires_human_review: bool = False


__all__ = [
    "CapabilityContractResponse", "AIResultEnvelope",
    "AIInvocationContext", "ProposedAction", "ProposedActionResponse",
    "ApproveProposedActionRequest", "RejectProposedActionRequest",
]
