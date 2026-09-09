"""
=========================================================
Homez OS

File : app/core/company_recovery_setup.py

HOMEZ Desktop 회사 초기 설정 "복구" 경로 — 2026-08-02 CTO 보안 보완
Gate R2.

`app/core/desktop_setup.py`(users=0일 때만 열리는 최초 관리자 설정)와
달리, 이 모듈은 "관리자 계정은 이미 있지만 소속 회사가 없는" 기존
DB를 위한 것이다(2026-08-02 실제 운영 DB에서 발견: sin9484@gmail.com
계정의 company_id가 NULL이고 companies 테이블이 완전히 비어 있던
상태 — Migration Runner 도입 이전, 회사 개념이 나중에 추가되면서
생긴 이력 공백).

허용 조건은 정확히 다음과 같을 때만이다(하나라도 어긋나면 거부):
  - companies 테이블이 정확히 0건
  - active=1이고 role이 SUPER_ADMIN이며 company_id가 NULL인 사용자가
    정확히 1명

두 조건 모두 원자적 Transaction(BEGIN IMMEDIATE) 안에서 재확인한 뒤
Company INSERT + 그 관리자의 company_id UPDATE + audit_log INSERT를
전부 같은 Transaction으로 처리한다 — TOCTOU 없이 정확히 한 번만
성공한다.

보안 방어 계층은 `desktop_setup.py`의 최초 관리자 설정과 완전히
동일한 것을 그대로 재사용한다(loopback, Desktop 모드+token,
Host/Origin, 일회용 setup nonce) — 이 화면도 일반 브라우저나 다른
프로세스에서 호출할 수 없어야 하는 "Desktop Shell 안에서만 허용되는
1회성 관리 작업"이라는 점에서 최초 관리자 설정과 정확히 같은 신뢰
등급이기 때문이다. 신규 설치(users=0)와 이 복구 경로(companies=0 &&
정확히 1명의 미연결 SUPER_ADMIN)는 서로 배타적인 실제 DB 상태에서만
활성화되므로, 같은 프로세스 전역 setup nonce 슬롯을 공유해도 두
화면이 동시에 유효한 시나리오는 없다.

클라이언트는 company_id/role_id/user_id 중 어느 것도 지정할 수
없다 — 요청 바디는 company_name과 setup_nonce뿐이며, 대상 관리자와
생성될 Company id는 전부 서버가 원자적 조회로 결정한다.
=========================================================
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from pydantic import BaseModel
from pydantic import Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.desktop_setup import require_desktop_mode_and_token
from app.core.desktop_setup import require_loopback
from app.core.desktop_setup import require_matching_origin
from app.core.first_admin_setup import resolve_sqlite_path
from app.core.setup_nonce import NonceCheckStatus
from app.core.setup_nonce import mark_nonce_consumed
from app.core.setup_nonce import verify_nonce
from app.domains.company.model import Company
from app.domains.role.model import Role
from app.domains.user.model import User

_BUSY_TIMEOUT_MS = 15000

router = APIRouter(prefix="/desktop-setup/company-recovery", tags=["Desktop Setup"])


# --------------------------------------------------
# 원자적 복구
# --------------------------------------------------

class CompanyRecoveryStatus(str, Enum):

    SUCCESS = "SUCCESS"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"


@dataclass(frozen=True)
class CompanyRecoveryResult:

    status: CompanyRecoveryStatus
    company_id: int | None = None
    user_id: int | None = None


def atomic_recover_company_and_link_admin(
    db_path: str, *, company_name: str,
) -> CompanyRecoveryResult:

    conn = sqlite3.connect(
        db_path, timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None,
    )

    try:
        conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        conn.execute("BEGIN IMMEDIATE")

        company_count = conn.execute(
            "SELECT COUNT(*) FROM companies",
        ).fetchone()[0]

        if company_count != 0:
            conn.execute("ROLLBACK")
            return CompanyRecoveryResult(status=CompanyRecoveryStatus.NOT_ELIGIBLE)

        eligible_admins = conn.execute(
            "SELECT u.id FROM users u "
            "JOIN roles r ON r.id = u.role_id "
            "WHERE UPPER(r.code) = 'SUPER_ADMIN' "
            "AND u.active = 1 AND u.company_id IS NULL",
        ).fetchall()

        if len(eligible_admins) != 1:
            # 대상이 0명(자격 없음) 또는 2명 이상(모호함) — 자동으로
            # 하나를 골라 연결하지 않는다. fail-closed.
            conn.execute("ROLLBACK")
            return CompanyRecoveryResult(status=CompanyRecoveryStatus.NOT_ELIGIBLE)

        admin_user_id = eligible_admins[0][0]

        try:
            company_cursor = conn.execute(
                "INSERT INTO companies "
                "(name, business_number, ceo, phone, email, address, active) "
                "VALUES (?, NULL, NULL, NULL, NULL, NULL, 1)",
                (company_name,),
            )
            company_id = company_cursor.lastrowid

            rowcount = conn.execute(
                "UPDATE users SET company_id = ? "
                "WHERE id = ? AND company_id IS NULL",
                (company_id, admin_user_id),
            ).rowcount

            if rowcount != 1:
                # 같은 Transaction 안에서는 원래 발생할 수 없지만(위에서
                # 이미 BEGIN IMMEDIATE로 배타 잠금을 쥔 뒤 재확인했음),
                # 그래도 방어적으로 0건이면 성공으로 간주하지 않는다.
                conn.execute("ROLLBACK")
                return CompanyRecoveryResult(status=CompanyRecoveryStatus.NOT_ELIGIBLE)

            conn.execute(
                "INSERT INTO audit_logs "
                "(company_id, user_id, action, entity, entity_id, description, "
                "ip_address) VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (
                    company_id, admin_user_id,
                    "RECOVER_COMPANY_AND_LINK_ADMIN", "companies",
                    str(company_id),
                    "Existing SUPER_ADMIN linked to newly created Company via "
                    "HOMEZ Desktop company-recovery screen",
                ),
            )

            conn.execute("COMMIT")

            return CompanyRecoveryResult(
                status=CompanyRecoveryStatus.SUCCESS,
                company_id=company_id, user_id=admin_user_id,
            )

        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK")
            return CompanyRecoveryResult(status=CompanyRecoveryStatus.NOT_ELIGIBLE)

        except Exception:
            conn.execute("ROLLBACK")
            raise

    finally:
        conn.close()


# --------------------------------------------------
# Schema
# --------------------------------------------------

class CompanyRecoveryStatusResponse(BaseModel):

    recovery_required: bool
    # 2026-08-02 Gate R6-B: 복구 화면이 "현재 관리자 이메일"을 읽기
    # 전용으로 표시해야 하므로 함께 반환한다 — recovery_required가
    # false면 항상 None(모호하거나 대상이 없는 상태에서는 어떤
    # 사용자 정보도 노출하지 않는다).
    admin_email: str | None = None


class CompanyRecoveryInitializeRequest(BaseModel):

    company_name: str = Field(..., min_length=1, max_length=100)
    setup_nonce: str


class CompanyRecoveryInitializeResponse(BaseModel):

    success: bool
    company_id: int
    user_id: int


# --------------------------------------------------
# 엔드포인트
# --------------------------------------------------

@router.get("/status", response_model=CompanyRecoveryStatusResponse)
def get_company_recovery_status(db: Session = Depends(get_db)):
    """
    로그인 여부와 무관하게(어떤 최초 설정 화면을 보여줄지 결정하는 데
    필요) 호출 가능해야 한다 — desktop_setup.py::get_setup_status와
    동일한 신뢰 수준.
    """

    company_count = db.query(Company).count()

    if company_count != 0:
        return CompanyRecoveryStatusResponse(recovery_required=False)

    eligible_users = (
        db.query(User)
        .join(Role, Role.id == User.role_id)
        .filter(func.upper(Role.code) == "SUPER_ADMIN")
        .filter(User.is_active.is_(True))
        .filter(User.company_id.is_(None))
        .all()
    )

    if len(eligible_users) != 1:
        return CompanyRecoveryStatusResponse(recovery_required=False)

    return CompanyRecoveryStatusResponse(
        recovery_required=True, admin_email=eligible_users[0].email,
    )


@router.post(
    "/initialize",
    response_model=CompanyRecoveryInitializeResponse,
    dependencies=[
        Depends(require_loopback),
        Depends(require_matching_origin),
        Depends(require_desktop_mode_and_token),
    ],
)
def initialize_company_recovery(
    data: CompanyRecoveryInitializeRequest,
    db: Session = Depends(get_db),
):

    nonce_status = verify_nonce(data.setup_nonce)

    if nonce_status != NonceCheckStatus.VALID:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"setup nonce 검증 실패: {nonce_status.value}",
        )

    company_name = data.company_name.strip()

    if not company_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="회사명을 입력해야 합니다.",
        )

    # `db`(get_db Session)가 위 조회로 연 read Transaction을 명시적으로
    # 끝낸다 — desktop_setup.py::initialize_first_admin과 동일한 이유
    # (열어둔 채로 두면 아래 원자적 sqlite3 커넥션의 배타적 쓰기 잠금과
    # 겹쳐 "database is locked"를 유발할 수 있다).
    db.commit()

    result = atomic_recover_company_and_link_admin(
        resolve_sqlite_path(), company_name=company_name,
    )

    if result.status == CompanyRecoveryStatus.NOT_ELIGIBLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="회사 초기 설정을 실행할 수 있는 상태가 아닙니다.",
        )

    mark_nonce_consumed()

    return CompanyRecoveryInitializeResponse(
        success=True, company_id=result.company_id, user_id=result.user_id,
    )


__all__ = [
    "router",
    "CompanyRecoveryStatus",
    "CompanyRecoveryResult",
    "atomic_recover_company_and_link_admin",
]
