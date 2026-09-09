"""
=========================================================
Homez OS

File : app/domains/decision/schema.py

HOMEZ V4 Decision AI — Pydantic 계약
=========================================================
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel
from pydantic import Field


class DecisionSupplementaryInputs(BaseModel):
    """
    ProductCandidate 자체 점수(trend/demand/competition/margin/risk/
    confidence)만으로는 채울 수 없는 축을 위한 선택적 보강 입력이다.
    제공하지 않으면 해당 축은 INSUFFICIENT_DATA로 처리된다 — 임의의
    긍정 점수를 만들지 않는다.
    """

    expected_margin_rate: Decimal | None = None
    gross_revenue: Decimal | None = None
    price_competitiveness_score: Decimal | None = Field(
        default=None, ge=0, le=100,
    )
    supply_stability_score: Decimal | None = Field(
        default=None, ge=0, le=100,
    )
    inventory_shipping_risk_score: Decimal | None = Field(
        default=None, ge=0, le=100,
    )
    return_claim_risk_score: Decimal | None = Field(
        default=None, ge=0, le=100,
    )
    brand_ip_risk_score: Decimal | None = Field(
        default=None, ge=0, le=100,
    )
    required_funding: Decimal | None = Field(default=None, ge=0)
    available_funding: Decimal | None = Field(default=None, ge=0)

    # 채널별 판매 방식(app/domains/marketplace_listing) 평가 전용 —
    # 항상 이 둘을 짝으로 넘긴다. fulfillment_mode만 있고 cost가
    # 없으면 MARGIN_AND_FEES 축은 INSUFFICIENT_DATA로 처리된다(0으로
    # 자동 보정하지 않는다) — 호출자가 항상 자신이 평가 중인 모드에
    # 정확히 대응하는 비용만 넘기므로, 로켓그로스 비용이 판매자배송
    # 평가에 섞이는 구조적 경로가 없다.
    fulfillment_mode: str | None = None
    fulfillment_specific_cost: Decimal | None = Field(default=None, ge=0)


class DecisionEvaluationRequest(BaseModel):

    candidate_id: int
    idempotency_key: str
    supplementary_inputs: DecisionSupplementaryInputs = Field(
        default_factory=DecisionSupplementaryInputs,
    )


class DecisionScoreResponse(BaseModel):

    id: int
    axis: str
    raw_score: Decimal
    weight: Decimal
    weighted_score: Decimal
    confidence: Decimal
    data_sufficient: bool
    risk_flag: bool
    evidence_text: str

    class Config:
        from_attributes = True


class DecisionEvaluationResponse(BaseModel):

    id: int
    company_id: int
    candidate_id: int
    # 2026-08-15 V7 Gate 2 — DecisionPolicy 전역/회사 분리 이후
    # policy_id(전역 템플릿 참조)는 policy_source="GLOBAL"일 때만
    # 채워진다. policy_source="COMPANY"인 평가는 company_policy_id를
    # 대신 확인해야 한다(감사 가능성 — 어느 정책 버전을 근거로
    # 평가됐는지 추적).
    policy_id: int | None = None
    company_policy_id: int | None = None
    policy_source: str
    policy_version: str
    input_fingerprint: str
    total_score: Decimal
    confidence: Decimal
    recommendation: str
    recommendation_reason: str
    blocked_by_safety: bool
    safety_block_reason: str | None
    status: str
    created_by: str
    evaluator_kind: str
    evaluator_version: str
    created_at: datetime

    class Config:
        from_attributes = True


class DecisionReviewRequest(BaseModel):

    idempotency_key: str
    memo: str | None = None


class DecisionOverrideRequest(BaseModel):

    idempotency_key: str
    override_reason: str
    new_value: str
    memo: str | None = None


class DecisionReviewResponse(BaseModel):

    id: int
    evaluation_id: int
    action: str
    reviewer_id: int
    memo: str | None
    override_reason: str | None
    previous_value: str | None
    new_value: str | None
    decided_at: datetime

    class Config:
        from_attributes = True


class DecisionPolicyCreateRequest(BaseModel):

    policy_set_id: str
    policy_version: str
    source_reference: str
    status: str = "DRAFT"
    is_complete: bool = False
    axis_weights: dict[str, Decimal]
    min_approve_total_score: Decimal = Decimal("70.0000")
    min_confidence_for_recommendation: Decimal = Decimal("0.5000")
    checked_at: datetime
    effective_at: datetime
    expires_at: datetime | None = None


class DecisionPolicyResponse(BaseModel):

    id: int
    policy_set_id: str
    policy_version: str
    source_reference: str
    status: str
    is_complete: bool
    is_active: bool
    min_approve_total_score: Decimal
    min_confidence_for_recommendation: Decimal
    checked_at: datetime
    effective_at: datetime
    expires_at: datetime | None
    created_at: datetime

    class Config:
        from_attributes = True


class DecisionCompanyPolicyCreateRequest(BaseModel):
    """
    회사별 적용 정책 생성 요청(2026-08-15 V7 Gate 2). company_id는
    요청 바디로 받지 않는다 — Router가 항상 current_user.company_id를
    사용한다(다른 회사 명의로 정책을 만드는 경로를 원천 차단).
    """

    policy_set_id: str
    policy_version: str
    source_reference: str
    status: str = "DRAFT"
    is_complete: bool = False
    axis_weights: dict[str, Decimal]
    min_approve_total_score: Decimal = Decimal("70.0000")
    min_confidence_for_recommendation: Decimal = Decimal("0.5000")
    checked_at: datetime
    effective_at: datetime
    expires_at: datetime | None = None
    # 선택적 계보 — 이 회사 정책이 특정 전역 템플릿을 복제해 만든
    # 것이라면 그 decision_policies.id를 남긴다(평가 로직에는 관여하지
    # 않는 순수 감사 정보).
    base_policy_id: int | None = None


class DecisionCompanyPolicyResponse(BaseModel):

    id: int
    company_id: int
    policy_set_id: str
    policy_version: str
    source_reference: str
    status: str
    is_complete: bool
    is_active: bool
    min_approve_total_score: Decimal
    min_confidence_for_recommendation: Decimal
    checked_at: datetime
    effective_at: datetime
    expires_at: datetime | None
    base_policy_id: int | None
    created_at: datetime

    class Config:
        from_attributes = True


class DecisionAuditLogResponse(BaseModel):

    id: int
    evaluation_id: int
    candidate_id: int
    event_type: str
    actor: str
    payload_summary: str
    occurred_at: datetime

    class Config:
        from_attributes = True


__all__ = [
    "DecisionSupplementaryInputs",
    "DecisionEvaluationRequest",
    "DecisionScoreResponse",
    "DecisionEvaluationResponse",
    "DecisionReviewRequest",
    "DecisionOverrideRequest",
    "DecisionReviewResponse",
    "DecisionPolicyCreateRequest",
    "DecisionPolicyResponse",
    "DecisionCompanyPolicyCreateRequest",
    "DecisionCompanyPolicyResponse",
    "DecisionAuditLogResponse",
]
