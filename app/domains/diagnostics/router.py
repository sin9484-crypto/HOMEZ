"""
=========================================================
Homez OS

File : app/domains/diagnostics/router.py

Gate Y-5(2026-08-12) — 진단 내보내기 API. admin_guard 전용(시스템
내부 정보를 담고 있으므로 다른 관리 전용 라우터와 동일한 보호
수준). 실제 운영 DB 경로는 `get_homez_db_path(confirm=True)`로만
얻는다 — 읽기 전용 진단만 하므로(PRAGMA integrity_check, Migration
diagnose 둘 다 읽기 전용) 안전하다.

2026-08-31 Preflight 8-A 감사 수정 — "안전하다(읽기 전용)"는 파괴적
위험이 없다는 뜻일 뿐, DATABASE_URL이 override된 상태에서는 여전히
실제 engine이 쓰는 파일과 다른 파일의 진단 결과를 "정상"으로
내보낼 수 있었다(오判定 위험). db-identity 엔드포인트가 이미 하던
것과 같은 원칙으로 `assert_bootstrap_path_matches_engine()`을 써서
불일치 시 명확히 실패하게 했다.
=========================================================
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends

from app.core.config import settings
from app.core.db_path_contract import assert_bootstrap_path_matches_engine
from app.core.guard import admin_guard
from app.desktop.paths import get_homez_db_path
from app.desktop.paths import get_logs_dir
from app.desktop.paths import get_repo_root
from app.domains.diagnostics.db_identity import collect_db_identity
from app.domains.diagnostics.schema import DbIdentityResponse
from app.domains.diagnostics.schema import DiagnosticsBundleResponse
from app.domains.diagnostics.service import build_diagnostics_bundle
from app.domains.user.model import User

router = APIRouter(
    prefix="/diagnostics",
    tags=["Diagnostics"],
)


@router.get(
    "/export",
    response_model=DiagnosticsBundleResponse,
)
def export_diagnostics(
    _: User = Depends(admin_guard),
):
    """앱 버전/OS/DB 무결성/Migration 상태/라우트 수/로그 tail(비밀정보 제거)을 한 번에 내보낸다."""

    from app.main import app as fastapi_app

    log_path = get_logs_dir() / "homez-launcher.log"

    bundle = build_diagnostics_bundle(
        db_path=assert_bootstrap_path_matches_engine(),
        migrations_dir=get_repo_root() / "migrations",
        route_count=len(fastapi_app.routes),
        log_path=log_path if log_path.exists() else None,
        settings=settings,
    )

    return bundle


@router.get(
    "/db-identity",
    response_model=DbIdentityResponse,
)
def db_identity(
    _: User = Depends(admin_guard),
):
    """
    2026-08-30 후속 지시 — 실제 SQLAlchemy engine이 물린 DB 파일의
    정체(경로/크기/mtime/SHA-256/NTFS 식별자)를 읽기 전용으로
    보고한다. admin 전용, Credential·사용자 개인정보 없음. 새
    DB 커넥션을 열거나 쓰기를 하지 않는다.
    """

    from app.database.session import get_engine_db_path

    bootstrap_db_path = None
    try:
        bootstrap_db_path = get_homez_db_path(confirm=True)
    except Exception:  # noqa: BLE001 — 경로 계산 실패해도 진단은 계속한다.
        bootstrap_db_path = None

    return collect_db_identity(
        engine_db_path=get_engine_db_path(),
        bootstrap_db_path=bootstrap_db_path,
    )


__all__ = ["router"]
