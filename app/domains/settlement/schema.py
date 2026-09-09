"""
=========================================================
Homez OS

File : app/domains/settlement/schema.py

Marketplace Settlement Schema
=========================================================
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class SettlementCreate(BaseModel):
    """마켓 정산 예정 생성."""

    market: str = Field(min_length=1, max_length=30)

    market_order_id: str = Field(min_length=1, max_length=100)

    order_id: Optional[int] = None

    account_id: int = Field(gt=0)

    gross_amount: float = Field(ge=0)

    fee_amount: float = Field(default=0, ge=0)

    net_amount: float = Field(gt=0)

    idempotency_key: Optional[str] = Field(
        default=None,
        max_length=120,
    )

    memo: Optional[str] = Field(default=None, max_length=500)


class SettlementMemoUpdate(BaseModel):
    """입금 확인 / 취소 / 환수 시 선택 메모."""

    memo: Optional[str] = Field(default=None, max_length=500)


class SettlementResponse(BaseModel):

    id: int

    market: str

    market_order_id: str

    order_id: Optional[int] = None

    account_id: int

    gross_amount: float

    fee_amount: float

    net_amount: float

    status: str

    deposited_at: Optional[datetime] = None

    funding_ledger_id: Optional[int] = None

    idempotency_key: str

    memo: Optional[str] = None

    created_at: datetime

    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


# --------------------------------------------------
# Gate AI-F2(2026-08-22) — 정산 차이 분석(읽기 전용).
# --------------------------------------------------

class SettlementDifferenceResponse(BaseModel):

    difference_type: str
    urgency: str
    target_entity: str
    evidence: str
    elapsed_hours: float
    recommended_action: str
    missing_evidence: list[str]
    approval_required: bool
    execution_allowed: bool


class SettlementDifferenceAnalysisResult(BaseModel):

    differences: list[SettlementDifferenceResponse]
    ai_result: dict


class SettlementDifferenceReviewActionCreate(BaseModel):
    """
    urgency/evidence/execution_allowed는 클라이언트에서 받지 않는다 —
    라우터가 analyze()를 다시 실행해 지금 이 순간의 실제 상태에서
    (difference_type, target_entity)가 일치하는 차이를 새로 찾아 그
    값만 사용한다(클라이언트가 EStop 상태나 긴급도를 자칭할 수 없다).
    """

    difference_type: str = Field(min_length=1, max_length=100)
    target_entity: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=120)


class ProposedActionSummaryResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    capability_code: str
    action_type: str
    target_entity: str
    status: str
    risk_level: str
    reason: str
    expires_at: Optional[datetime]
    created_at: datetime


class SettlementDifferenceReviewActionResponse(BaseModel):

    proposed_action: Optional[ProposedActionSummaryResponse]


__all__ = [
    "SettlementCreate",
    "SettlementMemoUpdate",
    "SettlementResponse",
    "SettlementDifferenceResponse",
    "SettlementDifferenceAnalysisResult",
    "SettlementDifferenceReviewActionCreate",
    "ProposedActionSummaryResponse",
    "SettlementDifferenceReviewActionResponse",
]
