"""
=========================================================
Homez OS

File : app/domains/backup/router.py

Gate Y-1(2026-08-12) — 백업 API. admin_guard 전용(운영자금/시스템
상태 등 다른 관리 전용 라우터와 동일한 보호 수준). 실제 운영 DB
경로는 `app.desktop.paths.get_homez_db_path(confirm=True)`로만
얻는다 — 이 라우터가 바로 그 문서가 말하는 "공식 승인된 백업 경로"
다. 클라이언트가 임의 경로를 지정할 수 있는 파라미터는 없다
(경로 주입 방지).

2026-08-31 Preflight 8-A 감사 수정 — 위 계산이 이 프로세스의 실제
SQLAlchemy engine(DATABASE_URL) 경로와 일치하는지 한 번도 확인하지
않았다(app/core/db_path_contract.py 모듈 docstring 참고). DATABASE_
URL이 기본값과 다르게 설정된 채 떠 있으면 엉뚱한 파일을 백업할 수
있었다 — `assert_bootstrap_path_matches_engine()`로 fail-closed
확인을 추가했다.
=========================================================
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from fastapi import status
from sqlalchemy.orm import Session

from app.core.db_path_contract import assert_bootstrap_path_matches_engine
from app.core.dependency import get_db
from app.core.exceptions import InternalServerException
from app.core.guard import admin_guard
from app.desktop.paths import get_backups_dir
from app.domains.backup.schema import BackupCreateRequest
from app.domains.backup.schema import BackupRecordResponse
from app.domains.backup.schema import BackupRetentionCheckResponse
from app.domains.backup.service import DEFAULT_RETENTION_KEEP_COUNT
from app.domains.backup.service import TRIGGER_SOURCE_MANUAL
from app.domains.backup.service import BackupError
from app.domains.backup.service import BackupService
from app.domains.user.model import User

router = APIRouter(
    prefix="/backups",
    tags=["Backup"],
)


@router.post(
    "",
    response_model=BackupRecordResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_backup(
    data: BackupCreateRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """실제 운영 DB의 온라인 백업을 지금 생성한다."""

    service = BackupService(db)

    try:
        return service.create_backup(
            source_db_path=assert_bootstrap_path_matches_engine(),
            backups_dir=get_backups_dir(),
            trigger_source=TRIGGER_SOURCE_MANUAL,
            triggered_by_user_id=current_user.id,
            label=data.label,
        )
    except BackupError as exc:
        raise InternalServerException(str(exc)) from exc


@router.get(
    "",
    response_model=list[BackupRecordResponse],
)
def list_backups(
    _: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """최근 백업 이력을 조회한다."""

    service = BackupService(db)

    return service.list_backups()


@router.get(
    "/retention-check",
    response_model=BackupRetentionCheckResponse,
)
def check_retention(
    keep_count: int = DEFAULT_RETENTION_KEEP_COUNT,
    _: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    2026-08-15 V7 Gate 8 — 보존 기준(keep_count, 최신순)을 넘는 백업
    이력을 조회만 한다 — 이 엔드포인트는 어떤 파일도 삭제하지
    않는다. 실제 정리는 이 목록을 확인한 운영자가 별도로 수행해야
    한다(자동 삭제는 이 세션 Safety 경계 밖).
    """

    service = BackupService(db)
    beyond = service.list_backups_beyond_retention(keep_count)
    total = len(service.list_backups(limit=10_000))

    return BackupRetentionCheckResponse(
        keep_count=keep_count,
        total_backups=total,
        beyond_retention_count=len(beyond),
        beyond_retention=beyond,
    )


__all__ = ["router"]
