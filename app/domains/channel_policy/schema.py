"""
=========================================================
Homez OS

File : app/domains/channel_policy/schema.py

채널 정책 엔진 — API 경계 Pydantic Schema. 완성된 한국어 문장은
저장·반환하지 않는다(listing_wizard_precheck.py와 동일 원칙) —
rule_code + 구조화 필드만 반환하고, 화면 i18n이 문구를 조립한다.
=========================================================
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from app.domains.ai_governance.schema import AIResultEnvelope


class RuleEvaluationDetail(BaseModel):
    """규칙 하나에 대한 평가 결과 — 재현 가능한 스냅샷 단위."""

    rule_code: str
    severity: str
    validation_type: str
    applies: bool
    satisfied: bool
    missing_fields: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    official_source_url: str | None = None
    source_title: str | None = None


class ChannelPolicyEvaluationResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    product_candidate_id: int
    channel: str
    result: str
    policy_profile_version: str
    rule_results: list[RuleEvaluationDetail]
    is_stale: bool
    created_at: datetime

    # Audit(2026-08-21, CTO 후속 지시) — 공통 AI 결과 계약(app/domains/
    # ai_governance/schema.py::AIResultEnvelope) 단계적 적용. 기존
    # 필드(result/rule_results 등)는 그대로 유지한다 — 이 필드는
    # 추가일 뿐 기존 계약을 대체하지 않는다.
    ai_result: AIResultEnvelope | None = None


class EvaluateChannelPolicyRequest(BaseModel):
    """정책 평가 요청 — product_candidate 자체는 category_hint만
    갖고 있으므로(자유 텍스트 AI 힌트), 그 외 구조화 사실(원산지,
    브랜드, 구매옵션, 상품식별번호, 확인된 증빙 등)은 호출자가 명시
    적으로 전달한다. 값이 없는 필드는 그냥 생략한다 — 0이나 빈
    문자열로 추정해 채우지 않는다."""

    product_candidate_id: int
    channel: str

    # CA-1 — 실제 MarketplaceFulfillmentSelection이 이미 만들어진
    # 이후(위저드 일괄 등록 이후)라면 반드시 넘긴다. 이 값이 있어야만
    # 이 평가가 제출 게이트를 통과할 수 있는 지문으로 저장된다 —
    # 없으면(위저드 초안 미리보기) 화면 안내용으로만 쓰인다.
    selection_id: int | None = None

    # 카테고리 매칭용 — 미지정 시 product_candidate.category_hint/
    # product_name을 그대로 사용한다.
    category_hint_override: str | None = None

    # STRUCTURAL_FIELD_REQUIRED 판정용 — 키가 없으면 "미입력"으로
    # 취급한다(0/빈문자열로 채우지 않는다).
    product_attributes: dict = Field(default_factory=dict)

    # CATEGORY_RESTRICTED_EVIDENCE/EVIDENCE_REQUIRED_GENERIC 판정용 —
    # 운영자가 이미 확인·업로드했다고 명시한 규칙 코드 목록.
    confirmed_evidence_rule_codes: list[str] = Field(default_factory=list)


class ChannelPolicyRuleSummary(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_code: str
    channel: str
    severity: str
    validation_type: str
    official_source_url: str | None
    source_title: str | None
    verified_at: datetime | None
    profile_version: str
    active: bool


class CompanyChannelPolicySettingsResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    min_target_margin_rate: Decimal | None
    min_profit_per_order: Decimal | None
    max_initial_purchase_amount: Decimal | None
    max_moq: int | None
    max_lead_time_days: int | None
    max_return_shipping_cost: Decimal | None
    allowed_categories: list[str]
    forbidden_categories: list[str]
    safety_stock_buffer: int | None
    version: int
    updated_at: datetime


class UpdateCompanyChannelPolicySettingsRequest(BaseModel):

    expected_version: int

    min_target_margin_rate: Decimal | None = None
    min_profit_per_order: Decimal | None = None
    max_initial_purchase_amount: Decimal | None = None
    max_moq: int | None = None
    max_lead_time_days: int | None = None
    max_return_shipping_cost: Decimal | None = None
    allowed_categories: list[str] | None = None
    forbidden_categories: list[str] | None = None
    safety_stock_buffer: int | None = None


class MarginEstimateInput(BaseModel):
    """상품 선별 단계의 예상 마진 입력 — 확인되지 않은 비용은 0으로
    묵시적으로 채우지 않는다. 필드를 아예 생략하면 '미확인'으로
    취급되어 결과의 provisional=True와 missing_cost_fields에 반영된다
    (listing_wizard의 EconomicsInputItem과는 별개 — 그쪽은 채널·
    방식 선택 이후의 확정 단계이고, 이건 선별 단계의 잠정 추정이다)."""

    sale_price: Decimal = Field(gt=0)
    cost_of_goods: Decimal | None = Field(default=None, ge=0)
    channel_fee_rate: Decimal | None = Field(default=None, ge=0, le=1)
    payment_fee_rate: Decimal | None = Field(default=None, ge=0, le=1)
    shipping_cost: Decimal | None = Field(default=None, ge=0)
    packaging_cost: Decimal | None = Field(default=None, ge=0)
    ad_cost: Decimal | None = Field(default=None, ge=0)
    return_reserve_rate: Decimal | None = Field(default=None, ge=0, le=1)
    tax_basis_rate: Decimal | None = Field(default=None, ge=0, le=1)


class MarginEstimateResult(BaseModel):

    expected_revenue: Decimal
    total_cost: Decimal
    margin_amount: Decimal
    margin_rate: Decimal
    break_even_price: Decimal | None
    is_provisional: bool
    missing_cost_fields: list[str]
    meets_company_target: bool | None
    company_target_margin_rate: Decimal | None


__all__ = [
    "RuleEvaluationDetail",
    "ChannelPolicyEvaluationResponse",
    "EvaluateChannelPolicyRequest",
    "ChannelPolicyRuleSummary",
    "CompanyChannelPolicySettingsResponse",
    "UpdateCompanyChannelPolicySettingsRequest",
    "MarginEstimateInput",
    "MarginEstimateResult",
]
