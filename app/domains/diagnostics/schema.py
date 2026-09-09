"""
=========================================================
Homez OS

File : app/domains/diagnostics/schema.py

Gate Y-5(2026-08-12) — 진단 내보내기 응답 스키마.
=========================================================
"""

from __future__ import annotations

from pydantic import BaseModel


class OsInfo(BaseModel):

    platform: str
    python_version: str


class DatabaseDiagnostics(BaseModel):

    exists: bool
    file_size_bytes: int | None
    integrity_check_result: str | None


class MigrationDiagnostics(BaseModel):

    status: str
    applied_count: int
    pending_count: int
    pending_files: list[str]
    backfill_needed_count: int | None = None
    error_type: str | None = None
    error_message: str | None = None


class DiagnosticsBundleResponse(BaseModel):

    generated_at: str
    app_version: str
    os: OsInfo
    database: DatabaseDiagnostics
    migrations: MigrationDiagnostics
    route_count: int
    log_tail: str | None


# 2026-08-30 후속 지시 — DB Identity 진단(읽기 전용, admin 전용).


class HomezDataRootEnv(BaseModel):

    is_set: bool
    masked_value: str | None = None


class WindowsFileIdentityResponse(BaseModel):

    available: bool
    volume_serial: int | None = None
    file_index: int | None = None
    hardlink_count: int | None = None
    error: str | None = None


class DbIdentityResponse(BaseModel):

    resolved_db_path: str | None
    exists: bool
    file_size_bytes: int | None = None
    mtime_utc: str | None = None
    sha256: str | None = None
    hash_skipped_reason: str | None = None
    sampled_at: str
    changed_during_measurement: bool | None = None
    identity_stable: bool
    homez_data_root_env: HomezDataRootEnv
    redirection_suspected: bool
    path_mismatch_warning: bool
    windows_file_identity: WindowsFileIdentityResponse


__all__ = [
    "OsInfo",
    "DatabaseDiagnostics",
    "MigrationDiagnostics",
    "DiagnosticsBundleResponse",
    "HomezDataRootEnv",
    "WindowsFileIdentityResponse",
    "DbIdentityResponse",
]
