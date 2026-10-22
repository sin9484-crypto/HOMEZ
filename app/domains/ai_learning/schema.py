"""
=========================================================
Homez OS

File : app/domains/ai_learning/schema.py

2026-09-10 Phase 12 — AI 학습 기반 API 스키마.
"""

from __future__ import annotations

from datetime import date
from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import StrictBool


class SuggestionRecordCreateRequest(BaseModel):
    """2026-10-23 M4-full — 채택해 쿠팡에 반영한 제안 1건 기록. 반영한 날을 모르면 오늘."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["PRODUCT_NAME", "SEARCH_KEYWORD", "PRICE"]
    suggested_value: str = Field(min_length=1, max_length=300)
    suggested_on: date | None = None
    product_ref: str | None = Field(default=None, max_length=80)
    vendor_item_id: str | None = Field(default=None, max_length=40)
    adopted: StrictBool = False


class SuggestionRecordUpdateRequest(BaseModel):
    """채택 표시·옵션 ID 연결만 바꾼다. 넘기지 않은 값은 그대로(vendor_item_id에 빈 글을 넘기면 연결 해제)."""

    model_config = ConfigDict(extra="forbid")

    adopted: StrictBool | None = None
    vendor_item_id: str | None = Field(default=None, max_length=40)


class RecordOutcomeRequest(BaseModel):

    evaluation_id: int
    order_id: int | None = None
    actual_sales_amount: float | None = None
    actual_margin_amount: float | None = None
    stockout: bool = False
    cancelled: bool = False
    returned: bool = False
    shipping_delayed: bool = False


class DecisionOutcomeResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    evaluation_id: int
    order_id: int | None
    actual_sales_amount: float | None
    actual_margin_amount: float | None
    stockout: bool
    cancelled: bool
    returned: bool
    shipping_delayed: bool
    recorded_at: datetime


class ExportDatasetRequest(BaseModel):

    evaluation_id: int


class LearningDatasetRecordResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    evaluation_id: int
    policy_version: str | None
    recommendation: str | None
    user_decision: str | None
    actual_sales_amount: float | None
    actual_margin_amount: float | None
    stockout: bool
    cancelled: bool
    returned: bool
    shipping_delayed: bool
    exported_at: datetime


class LearningReadinessResponse(BaseModel):

    ready: bool
    reason: str | None
    available: int
    required: int


class ModelCandidateCreateRequest(BaseModel):

    name: str
    version: str


class ModelCandidateResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    version: str
    status: str
    sample_size_used: int | None
    offline_eval_summary: str | None
    regression_comparison_summary: str | None
    approved_by: int | None
    approved_at: datetime | None
    created_at: datetime


class MarkOfflineEvaluatedRequest(BaseModel):

    summary: str
    sample_size_used: int


class MarkRegressionComparedRequest(BaseModel):

    summary: str


class RejectModelCandidateRequest(BaseModel):

    reason: str
