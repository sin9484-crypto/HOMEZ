"""
=========================================================
Homez OS

File : app/domains/backup/schema.py

Gate Y-1(2026-08-12) — 백업 API 요청/응답 스키마.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class BackupCreateRequest(BaseModel):

    label: str | None = Field(
        default=None,
        max_length=200,
    )


class BackupRecordResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    file_path: str
    file_size_bytes: int
    sha256: str
    integrity_check_result: str
    trigger_source: str
    triggered_by_user_id: int | None
    label: str | None
    is_encrypted: bool
    created_at: datetime


class BackupRetentionCheckResponse(BaseModel):
    """
    2026-08-15 V7 Gate 8 — 보존 정책 조회 응답. 읽기 전용이다 —
    `beyond_retention`은 삭제 후보를 "보여줄" 뿐, 이 API 자체가
    삭제를 수행하지 않는다.
    """

    keep_count: int
    total_backups: int
    beyond_retention_count: int
    beyond_retention: list[BackupRecordResponse]


__all__ = [
    "BackupCreateRequest",
    "BackupRecordResponse",
    "BackupRetentionCheckResponse",
]
