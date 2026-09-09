"""
=========================================================
Homez OS

File : app/domains/funding/schema.py

Funding Schema — 사업 운영자금
=========================================================
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class FundingAccountCreate(BaseModel):
    """
    사업 운영자금 최초 등록.

    2026-08-15 V7 Gate 2 — company_id를 요청 바디에서 제거했다(요구사항
    3). Router가 항상 current_user.company_id에서만 가져온다 — 인증된
    admin이 임의의 company_id를 지정해 타사 명의로 계정을 만드는 경로를
    원천 차단한다(실제로 존재했던 크로스테넌트 결함, 완료 보고서 참고).
    """

    total_funding: float = Field(gt=0)

    currency: str = Field(default="KRW", max_length=10)


class FundingAmountUpdate(BaseModel):
    """사업 운영자금 추가/회수."""

    amount: float = Field(gt=0)

    memo: Optional[str] = Field(default=None, max_length=500)


class FundingAccountResponse(BaseModel):

    id: int

    company_id: Optional[int] = None

    total_funding: float

    held_amount: float

    available_amount: float

    currency: str

    created_at: datetime

    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


class FundingLedgerResponse(BaseModel):

    id: int

    company_id: int

    account_id: int

    amount: float

    type: str

    reference_type: Optional[str] = None

    reference_id: Optional[int] = None

    memo: Optional[str] = None

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


class FundingHoldResponse(BaseModel):

    id: int

    company_id: int

    account_id: int

    order_id: int

    purchase_id: Optional[int] = None

    amount: float

    status: str

    idempotency_key: str

    created_at: datetime

    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


class SupplierPaymentConfirm(BaseModel):
    """공급처 지급 수동 확정."""

    memo: Optional[str] = Field(default=None, max_length=500)


class SupplierPaymentResponse(BaseModel):

    id: int

    company_id: int

    purchase_id: int

    order_id: int

    supplier_id: int

    account_id: int

    hold_id: int

    amount: float

    status: str

    paid_at: datetime

    idempotency_key: str

    memo: Optional[str] = None

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


class FundingMessage(BaseModel):

    message: str


__all__ = [
    "FundingAccountCreate",
    "FundingAmountUpdate",
    "FundingAccountResponse",
    "FundingLedgerResponse",
    "FundingHoldResponse",
    "SupplierPaymentConfirm",
    "SupplierPaymentResponse",
    "FundingMessage",
]
