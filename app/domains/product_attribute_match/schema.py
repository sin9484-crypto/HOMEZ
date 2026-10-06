"""
=========================================================
Homez OS

File : app/domains/product_attribute_match/schema.py

2026-09-15 전면 감사 후속(Phase 9G, 10-4).
=========================================================
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class ProductAttributeComparisonItemResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    field_name: str

    supplier_value: Optional[str] = None
    supplier_source: Optional[str] = None
    supplier_confirmed_at: Optional[datetime] = None

    sales_channel_value: Optional[str] = None
    sales_channel_source: Optional[str] = None
    sales_channel_confirmed_at: Optional[datetime] = None

    homez_current_value: Optional[str] = None
    homez_current_source: Optional[str] = None
    homez_current_confirmed_at: Optional[datetime] = None

    match_status: str
    selected_value: Optional[str] = None
    # 자동 등록/발주를 막는 6개 필드인지 — 아니면 미확인이어도 해소를 요구하지 않는다.
    is_blocking_field: bool = False


class ProductAttributeComparisonRunResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    product_identifier: str
    connection_id: Optional[int] = None
    overall_status: str
    created_at: datetime
    resolved_by: Optional[int] = None
    resolved_at: Optional[datetime] = None
    resolution_note: Optional[str] = None
    items: list[ProductAttributeComparisonItemResponse] = []
    # 저장된 값이 아니라 계산한 값: 같은 비교 내용의 이전 해소 run(있으면 그 id),
    # 지금 실제로 차단 중인지, 해소와 별개로 미확인인 비차단 필드.
    covered_by_run_id: Optional[int] = None
    blocking_active: bool = False
    non_blocking_unconfirmed_fields: list[str] = []


class AttributeSourceValueInput(BaseModel):

    value: Optional[str] = None
    source: Optional[str] = None
    confirmed_at: Optional[datetime] = None


class RunComparisonRequest(BaseModel):

    product_identifier: str = Field(min_length=1)
    connection_id: Optional[int] = None
    supplier_values: dict[str, AttributeSourceValueInput] = {}
    sales_channel_values: dict[str, AttributeSourceValueInput] = {}
    homez_current_values: dict[str, AttributeSourceValueInput] = {}


class ResolveComparisonRunRequest(BaseModel):

    resolution_note: str = Field(min_length=1)
    selected_values: dict[int, str]
