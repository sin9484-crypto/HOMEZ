"""
=========================================================
Homez OS

File : app/domains/restore/router.py

Gate Y-2(2026-08-12) — 복원 API. 검증(읽기 전용)과 이력 조회.

2026-08-15 V7 Gate 8 — 실행 엔드포인트를 추가할 때는
`service.py::require_app_closed_confirmation(confirmed)`를 실행
직전에 반드시 통과시켜야 한다(요청 바디의 명시적
`app_closed_confirmed: bool` 필드를 그대로 넘긴다 — fail-closed,
기본 False로 두면 절대 통과하지 않는다).

V7 Live Gate 4 복원 결함 수정(2026-08-17) — Gate R-2. 실행
엔드포인트를 신설한다. Gate R-0/R-1 결론에 따라 **이 엔드포인트는
`RestoreService.restore()`를 이 살아있는 서버 프로세스 안에서 동기
호출하지 않는다** — HOMEZ Desktop은 멀티스레드 FastAPI 서버라
자기 자신의 커넥션만 정리해도 다른 요청 스레드가 전역 engine에서
새 커넥션을 체크아웃하는 경쟁(TOCTOU)을 막을 수 없기 때문이다
(계측 근거: scratchpad live_gate4_restore_fix_progress.md Gate R-0).
대신 이 엔드포인트는 복원 계획을 저장하고
`app.desktop.restore_helper.request_restore_shutdown()`을 호출해
HOMEZ 전체를 정상 종료시킨 뒤, 완전히 별도인 Restore Helper
프로세스가 종료 완료를 확인한 뒤에만 실제 파일 교체를 수행하고
HOMEZ를 재기동한다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from uuid import uuid4

from fastapi import APIRouter
from fastapi import BackgroundTasks
from fastapi import Depends
from fastapi import status
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.core.guard import admin_guard
from app.core.windows_credential_store import CredentialStore
from app.core.windows_credential_store import WindowsCredentialStore
from app.domains.backup.repository import BackupRepository
from app.domains.restore.schema import BackupValidationRequest
from app.domains.restore.schema import BackupValidationResponse
from app.domains.restore.schema import RestoreAttemptResponse
from app.domains.restore.schema import RestoreExecuteRequest
from app.domains.restore.schema import RestoreExecuteResponse
from app.domains.restore.service import RestoreError
from app.domains.restore.service import RestoreService
from app.domains.restore.service import require_app_closed_confirmation
from app.domains.user.model import User

router = APIRouter(
    prefix="/restores",
    tags=["Restore"],
)


def get_credential_store() -> CredentialStore:

    return WindowsCredentialStore()


@router.post(
    "/validate",
    response_model=BackupValidationResponse,
)
def validate_backup(
    data: BackupValidationRequest,
    _: User = Depends(admin_guard),
    db: Session = Depends(get_db),
    credential_store: CredentialStore = Depends(get_credential_store),
):
    """지정한 백업 이력 행이 실제로 복원 가능한 상태인지 읽기 전용으로 검증한다."""

    backup_repository = BackupRepository(db)
    record = backup_repository.get(data.backup_record_id)

    if record is None:
        raise NotFoundException("해당 백업 이력을 찾을 수 없습니다.")

    service = RestoreService(db, credential_store)
    result = service.validate_backup_file(
        backup_path=record.file_path,
        expected_sha256=record.sha256,
    )

    return BackupValidationResponse(
        backup_record_id=record.id,
        **result,
    )


@router.post(
    "",
    response_model=RestoreExecuteResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def execute_restore(
    data: RestoreExecuteRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
    credential_store: CredentialStore = Depends(get_credential_store),
):
    """
    실 운영 DB(homez.db)에 대한 복원을 실제로 실행한다.

    이 핸들러 자신은 파일을 교체하지 않는다 — 백업을 읽기 전용으로
    재검증하고, `app_closed_confirmed`를 fail-closed로 확인한 뒤,
    복원 계획을 저장하고 Restore Helper 프로세스를 기동해 HOMEZ
    전체를 종료시킨다(모듈 상단 docstring의 Gate R-0/R-1 근거 참고).
    HTTP 응답이 클라이언트에 전달된 "이후"에 창을 닫도록
    `BackgroundTasks`로 미룬다 — 응답 전송 도중 서버가 죽는 것을
    피하기 위함이다.
    """

    backup_repository = BackupRepository(db)
    record = backup_repository.get(data.backup_record_id)

    if record is None:
        raise NotFoundException("해당 백업 이력을 찾을 수 없습니다.")

    service = RestoreService(db, credential_store)
    validation = service.validate_backup_file(
        backup_path=record.file_path,
        expected_sha256=record.sha256,
    )

    if not validation["restorable"]:
        raise BadRequestException(
            validation["reason"] or "백업 검증에 실패해 복원을 실행할 수 없습니다.",
        )

    try:
        require_app_closed_confirmation(data.app_closed_confirmed)
    except RestoreError as exc:
        raise BadRequestException(str(exc)) from exc

    from app.core.db_path_contract import assert_bootstrap_path_matches_engine
    from app.desktop import restore_helper
    from app.desktop.paths import get_backups_dir

    # 2026-08-31 Preflight 8-A 감사 수정 — 이 값을 그대로
    # get_homez_db_path(confirm=True)로만 계산했었다. 복원은 파일을
    # 통째로 덮어쓰는 작업이라, 이 프로세스의 실제 SQLAlchemy engine
    # 경로와 다르면(DATABASE_URL이 기본값과 다르게 설정된 채 떠
    # 있으면) 엉뚱한 파일을 덮어쓸 수 있었다 — fail-closed로 막는다.
    plan = restore_helper.RestorePlan(
        plan_id=str(uuid4()),
        source_backup_path=record.file_path,
        target_db_path=str(assert_bootstrap_path_matches_engine()),
        expected_sha256=record.sha256,
        pre_restore_backups_dir=str(get_backups_dir() / "pre_restore"),
        triggered_by_user_id=current_user.id,
        requested_at=datetime.now(timezone.utc).isoformat(),
        relaunch_argv=restore_helper.build_relaunch_argv(),
    )
    plan_path = restore_helper.default_plan_path()

    def _trigger_shutdown_and_helper() -> None:
        restore_helper.request_restore_shutdown(plan, plan_path)

    background_tasks.add_task(_trigger_shutdown_and_helper)

    return RestoreExecuteResponse(
        plan_id=plan.plan_id,
        message=(
            "복원 요청이 접수되었습니다. HOMEZ가 곧 종료되고, 복원 "
            "완료 후 자동으로 다시 시작됩니다. "
            "(Restore request accepted. HOMEZ will close shortly and "
            "restart automatically after the restore completes.)"
        ),
    )


@router.get(
    "",
    response_model=list[RestoreAttemptResponse],
    status_code=status.HTTP_200_OK,
)
def list_restore_attempts(
    _: User = Depends(admin_guard),
    db: Session = Depends(get_db),
    credential_store: CredentialStore = Depends(get_credential_store),
):
    """복원 시도 이력(성공/실패 모두)을 조회한다."""

    service = RestoreService(db, credential_store)

    return service.list_attempts()


__all__ = ["router"]
