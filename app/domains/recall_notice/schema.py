"""
=========================================================
Homez OS

File : app/domains/recall_notice/schema.py

2026-09-15 전면 감사 후속(Phase 9I/9J, 10-17/10-18).
=========================================================
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class RecallCheckJobModeResponse(BaseModel):

    mode: str


class RecallCheckJobModeSetRequest(BaseModel):

    mode: str = Field(min_length=1)


class RecallCheckRunResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_name: str
    status: str
    notices_found_count: int
    new_notices_count: int
    error_detail: Optional[str] = None
    started_at: datetime
    finished_at: Optional[datetime] = None


class RecallProductBlockResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    product_identifier: str
    recall_notice_id: Optional[int] = None
    status: str
    reason: str
    blocked_at: datetime
    unblock_requested_by: Optional[int] = None
    unblock_justification: Optional[str] = None
    unblock_approved_by: Optional[int] = None
    unblock_approved_at: Optional[datetime] = None


class UnblockRecallProductRequest(BaseModel):

    justification: str = Field(min_length=1)
