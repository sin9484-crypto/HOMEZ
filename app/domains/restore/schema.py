"""
=========================================================
Homez OS

File : app/domains/restore/schema.py

Gate Y-2(2026-08-12) — 복원 API 요청/응답 스키마.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict


class BackupValidationRequest(BaseModel):

    backup_record_id: int


class BackupValidationResponse(BaseModel):

    backup_record_id: int
    file_exists: bool
    sha256_matches: bool
    integrity_check_result: str | None
    restorable: bool
    reason: str | None = None


class RestoreExecuteRequest(BaseModel):
    """
    V7 Live Gate 4 복원 결함 수정(2026-08-17) — Gate R-2. 실제 복원
    실행 요청. `app_closed_confirmed`는 기본값 False(생략 시 항상
    차단, fail-closed) — 사용자가 "HOMEZ가 지금 종료되고 복원 후
    자동으로 재시작된다"는 것을 명시적으로 이해했다는 확인이다
    (`app/domains/restore/service.py::require_app_closed_confirmation`
    참고. Gate R-1 Option B 채택 이후에도 이 확인 자체는 계속
    fail-closed로 유지한다 — 종료·재시작이 자동화됐다는 것이
    "사용자 동의 없이 실행해도 된다"는 뜻은 아니다).
    """

    backup_record_id: int
    app_closed_confirmed: bool = False


class RestoreExecuteResponse(BaseModel):

    plan_id: str
    message: str


class RestoreAttemptResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    source_backup_path: str
    target_db_path: str
    pre_restore_backup_path: str | None
    status: str
    integrity_check_result: str | None
    error_message: str | None
    file_size_bytes: int | None
    triggered_by_user_id: int | None
    created_at: datetime


__all__ = [
    "BackupValidationRequest",
    "BackupValidationResponse",
    "RestoreExecuteRequest",
    "RestoreExecuteResponse",
    "RestoreAttemptResponse",
]
