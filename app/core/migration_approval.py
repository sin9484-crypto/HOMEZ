"""
=========================================================
Homez OS

File : app/core/migration_approval.py

2026-08-05 CTO 재검증 지시 Gate E — Migration 승인 UX.

배경(결함 DEFECT-MIGRATION-AUTOAPPLY-UX-001): 이전에는 Desktop 앱을
시작하기만 해도 `bootstrap_environment()`가 진단 → 필요 시 백업 →
곧바로 미적용 Migration을 적용했다 — 사용자에게 적용 범위·백업 경로·
복구 계획을 사전에 보여주거나 명시적 승인을 받는 절차가 전혀 없었다.
실제로 이 경로를 통해(누가 실행했는지는 확정할 수 없음) 신규
Migration이 사용자 승인 없이 실제 homez.db에 적용된 사고가 있었다
(자세한 내용은 docs/V6_EXECUTION_LEDGER.md "긴급 발견 2" 참고).

이 모듈은 그 자동 적용을 막고, 대신 두 엔드포인트로 나눈다:
  - `GET /desktop-setup/migration-status`: 순수 읽기 전용 진단 —
    적용 대상 파일명·테이블/인덱스·예상 백업 경로만 보여준다. DB에
    어떤 쓰기도 하지 않는다.
  - `POST /desktop-setup/migration-status/approve`: 사용자가 위
    화면에서 명시적으로 승인한 뒤에만 호출된다 — 그 시점의 pending
    목록과 정확히 같은 파일 목록을 다시 보내야 하고(부분 승인·오래된
    승인 재사용 방지), `app/core/desktop_setup.py`와 동일한 Desktop
    전용 방어 계층(loopback + Origin 일치 + Desktop 모드 토큰)을
    요구한다.

`app/database/bootstrap.py::bootstrap_environment()`가 실제 승인
게이트 로직(정확히 일치하는 파일 목록만 적용, 백업, integrity_check,
감사 로그)을 갖고 있다 — 이 모듈은 그 위에 얇은 HTTP 계약만 씌운다.
=========================================================
"""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from fastapi import HTTPException
from fastapi import status
from pydantic import BaseModel

from app.core.desktop_setup import require_desktop_mode_and_token
from app.core.desktop_setup import require_loopback
from app.core.desktop_setup import require_matching_origin
from app.core.guard import SuperAdminGuard
from app.core.migration_approval_nonce import MigrationApprovalNonceStatus
from app.core.migration_approval_nonce import generate_migration_approval_nonce
from app.core.migration_approval_nonce import mark_nonce_consumed
from app.core.migration_approval_nonce import verify_nonce as verify_approval_nonce
from app.core.migration_restricted_mode import refresh_restricted_mode_state
from app.core.recent_auth import consume_recent_auth_token
from app.database.bootstrap import bootstrap_environment
from app.database.migration_runner import MigrationRunner
from app.domains.user.model import User

router = APIRouter(prefix="/desktop-setup", tags=["Desktop Setup"])

# Gate G(2026-08-07) — 동시에 여러 승인 요청이 들어와도 정확히 하나만
# 실제 적용을 진행한다. 대기시키지 않고(블로킹 없이) 즉시 423으로
# 거절한다 — 이미 진행 중인 적용이 있다는 뜻이므로 재시도는 그 결과를
# 본 뒤에 하는 것이 안전하다.
_apply_lock = threading.Lock()


# --------------------------------------------------
# Schema
# --------------------------------------------------

class MigrationTargetsResponse(BaseModel):

    tables: list[str]
    indexes: list[str]


class MigrationStatusResponse(BaseModel):

    approval_required: bool
    pending_files: list[str] = []
    targets_by_file: dict[str, MigrationTargetsResponse] = {}
    backup_path_preview: str | None = None
    approval_nonce: str | None = None


class MigrationApproveRequest(BaseModel):

    approved_files: list[str]
    approval_nonce: str


class MigrationApproveResponse(BaseModel):

    applied: list[str]
    backup_path: str | None
    integrity_check_result: str | None
    approval_required: bool


# --------------------------------------------------
# 내부 헬퍼 — 실제 저장소 경로 계약(app/desktop/paths)에 결합
# --------------------------------------------------

def _real_paths() -> tuple[Path, Path, Path]:
    """
    2026-08-20 4차 지시(DB 경로 단일 계약) — `migration_restricted_mode.
    py::_real_migration_paths()`와 정확히 같은 이유로 같은 방식으로
    고친다: 이 승인 화면이 보여줘야 하는 DB는 "지금 이 서버 프로세스가
    실제로 요청을 처리하는 그 DB"이므로, `app/database/session.py`가
    엔진을 만들 때 쓰는 것과 동일한 `settings.DATABASE_URL`을
    `resolve_sqlite_path()`로 해석한다. DATABASE_URL을 override하지
    않은 기본 실행에서는 `_default_database_url()`이 이미
    `get_homez_db_path(confirm=True)`와 동일한 절대경로를 계산하므로
    기본 동작은 그대로다 — override된 경우에만 그 override를 실제로
    존중한다(이전에는 여기서도 DATABASE_URL을 완전히 무시했다).
    `backups_dir`는 실제 운영 백업 보관소 계약(app/desktop/paths.py)을
    그대로 유지한다 — 이 함수가 만드는 백업 "미리보기 경로 문자열"
    자체는 DB에 아무것도 쓰지 않으므로 격리 여부와 무관하다.
    """

    from app.core.first_admin_setup import resolve_sqlite_path
    from app.desktop import paths

    db_path = Path(resolve_sqlite_path())
    migrations_dir = paths.get_repo_root() / "migrations"
    backups_dir = paths.get_backups_dir()

    return db_path, migrations_dir, backups_dir


# --------------------------------------------------
# 엔드포인트
# --------------------------------------------------

@router.get("/migration-status", response_model=MigrationStatusResponse)
def get_migration_status() -> MigrationStatusResponse:
    """
    읽기 전용 — `mode=ro` + `PRAGMA query_only=ON`으로만 조회한다.
    로그인 여부와 무관하게 호출 가능해야 한다(콘솔 셸을 보여주기 전에
    먼저 이 상태를 확인해야 하므로 — `/desktop-setup/status`와 동일한
    신뢰 수준).
    """

    import sqlite3

    db_path, migrations_dir, backups_dir = _real_paths()

    if not db_path.exists():
        # 신규 설치는 bootstrap_environment()가 항상 전체를 즉시
        # 순차 적용한다(보호할 기존 데이터가 없음) — 승인 대상 없음.
        return MigrationStatusResponse(approval_required=False)

    runner = MigrationRunner(db_path, migrations_dir)

    ro_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    ro_conn.execute("PRAGMA query_only = ON")
    try:
        diagnosis = runner.diagnose(ro_conn)
    finally:
        ro_conn.close()

    pending = diagnosis["pending"]

    if not pending:
        return MigrationStatusResponse(approval_required=False)

    targets = runner.describe_pending_targets(pending)
    from datetime import datetime

    backup_preview = (
        backups_dir
        / f"homez_pre_bootstrap_migration_"
          f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    )

    # Gate G(2026-08-07): 이 상태 화면을 볼 때마다 새 단발성 승인
    # nonce를 발급한다 — 실제 승인(POST .../approve)은 이 nonce를
    # 반드시 제시해야 하고, 한 번 쓰면 재사용할 수 없다. 이전 화면
    # 조회에서 받은 nonce는 이 호출로 자동 무효화된다.
    approval_nonce = generate_migration_approval_nonce()

    return MigrationStatusResponse(
        approval_required=True,
        pending_files=list(pending),
        targets_by_file={
            fn: MigrationTargetsResponse(
                tables=t.get("tables", []), indexes=t.get("indexes", []),
            )
            for fn, t in targets.items()
        },
        backup_path_preview=str(backup_preview),
        approval_nonce=approval_nonce,
    )


@router.post(
    "/migration-status/approve",
    response_model=MigrationApproveResponse,
    dependencies=[
        Depends(require_loopback),
        Depends(require_matching_origin),
        Depends(require_desktop_mode_and_token),
    ],
)
def approve_migration(
    data: MigrationApproveRequest,
    current_user: User = Depends(SuperAdminGuard),
    recent_auth_token: str | None = Header(
        default=None, alias="X-Recent-Auth-Token",
    ),
) -> MigrationApproveResponse:
    """
    실제 쓰기가 일어나는 유일한 지점 — Gate G(2026-08-07)부터 다음을
    모두 통과해야만 도달한다.

      - loopback + Origin 일치 + Desktop 모드 토큰(라우터 dependencies,
        기존 Gate E부터).
      - SUPER_ADMIN 권한(`SuperAdminGuard` — 로그인 자체도 여기서
        같이 요구된다).
      - recent-auth(`X-Recent-Auth-Token`, /auth/recent-auth에서
        발급, 5분·1회용) — 방금 비밀번호로 재확인했다는 증거.
      - 단발성 승인 nonce(`approval_nonce`, GET .../migration-status
        조회마다 새로 발급) — 실제로 그 상태 화면을 본 뒤에만 승인할
        수 있다.
      - 동시 요청 중 정확히 하나만 실제 적용을 진행한다(비블로킹 락).

    `bootstrap_environment()`가 요청된 `approved_files`와 그 시점의
    실제 pending 목록이 정확히 일치할 때만 적용한다 — 다르면 조용히
    무시하지 않고 여전히 `approval_required=True`로 응답한다.
    """

    if not data.approved_files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="승인할 Migration 파일 목록이 비어 있습니다.",
        )

    if not consume_recent_auth_token(recent_auth_token, current_user.id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Migration 적용 전 현재 비밀번호로 다시 확인해야 합니다.",
        )

    nonce_status = verify_approval_nonce(data.approval_nonce)
    if nonce_status != MigrationApprovalNonceStatus.VALID:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "승인 요청이 유효하지 않습니다 — Migration 상태 화면을 "
                f"다시 불러온 뒤 시도하세요({nonce_status.value})."
            ),
        )

    if not _apply_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="다른 Migration 적용이 이미 진행 중입니다 — 완료 후 다시 시도하세요.",
        )

    try:
        db_path, migrations_dir, backups_dir = _real_paths()

        try:
            result = bootstrap_environment(
                db_path=db_path, migrations_dir=migrations_dir,
                backups_dir=backups_dir,
                approved_migration_files=data.approved_files,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Migration 적용 중 오류가 발생했습니다 — 적용 전 자동"
                    "백업에서 복구할 수 있습니다. 상세 원인은 서버 로그를"
                    "확인하세요."
                ),
            ) from exc

        if result.applied:
            # 실제로 뭔가 적용된 경우에만 승인 nonce를 소비한다 —
            # 적용된 파일이 하나도 없으면(예: 목록 불일치로 조용히
            # 거부된 경우) 재시도할 수 있도록 nonce를 살려둔다.
            mark_nonce_consumed()
            # 방금 적용한 파일이 더 이상 pending에 남지 않도록 제한
            # 모드 캐시를 즉시 재계산한다(다음 재시작까지 기다리지
            # 않는다).
            refresh_restricted_mode_state()

        return MigrationApproveResponse(
            applied=result.applied,
            backup_path=str(result.backup_path) if result.backup_path else None,
            integrity_check_result=result.integrity_check_result,
            approval_required=result.migration_approval_required,
        )
    finally:
        _apply_lock.release()


__all__ = [
    "router",
]
