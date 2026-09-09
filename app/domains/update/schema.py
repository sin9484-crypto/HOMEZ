"""
=========================================================
Homez OS

File : app/domains/update/schema.py

Gate Y-4(2026-08-12) — 업데이트 공지 API 요청/응답 스키마.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class UpdateNoticeCreateRequest(BaseModel):

    version: str = Field(max_length=50)
    title: str = Field(max_length=200)
    message: str
    severity: str
    release_notes_url: str | None = Field(default=None, max_length=500)


class UpdateNoticeResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    version: str
    title: str
    message: str
    severity: str
    release_notes_url: str | None
    is_active: bool
    published_by_user_id: int | None
    created_at: datetime


class UpdateStatusResponse(BaseModel):

    current_version: str
    update_available: bool
    latest_notice: UpdateNoticeResponse | None = None


__all__ = [
    "UpdateNoticeCreateRequest",
    "UpdateNoticeResponse",
    "UpdateStatusResponse",
]
