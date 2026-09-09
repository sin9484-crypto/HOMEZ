"""
=========================================================
Homez OS

File : app/core/desktop_setup.py

HOMEZ Desktop 최초 관리자(SUPER_ADMIN) 설정 API.

실제 `users` 테이블이 0건일 때만 단 한 번 허용되는 계정 생성
경로다. "users가 0이면 공개 API로 허용"이 아니라, 다음을 모두
동시에 만족해야만 실제 INSERT를 시도한다(2026-07-30 설계):

  1. 요청이 loopback(127.0.0.1/::1)에서 옴
  2. 이 프로세스가 Desktop 모드로 실행 중(Desktop token이 설정돼 있음)
  3. 유효한 Desktop session token 쿠키 제시
  4. pywebview js_api로만 전달 가능한 일회용 setup nonce 제시
  5. 실제 users 행 수가 정확히 0(BEGIN IMMEDIATE로 원자적 확인)
  6. SUPER_ADMIN 역할이 실제 존재
  7. nonce가 아직 소비되지 않음
  8. Host/Origin이 이 로컬 서버와 일치(외부 Host 거부)
  9. 비밀번호 정책 통과
  10. 비밀번호 확인 값 일치

Desktop token은 사용자 인증을 대체하지 않는다 — 이 API 자체가
"인증되지 않은 최초 1회" 계정 생성이므로, 사용자 로그인(admin_guard)을
요구하지 않는 것이 의도된 동작이다(로그인할 계정이 아직 없으므로).
=========================================================
"""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter
from fastapi import Cookie
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from pydantic import BaseModel
from pydantic import Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependency import get_db
from app.core.desktop_auth import DESKTOP_TOKEN_COOKIE_NAME
from app.core.desktop_token import get_desktop_token
from app.core.first_admin_setup import FirstAdminSetupStatus
from app.core.first_admin_setup import atomic_create_first_admin
from app.core.first_admin_setup import resolve_sqlite_path
from app.core.password_policy import validate_password_policy
from app.core.security import constant_time_compare
from app.core.security import hash_password
from app.core.setup_nonce import NonceCheckStatus
from app.core.setup_nonce import mark_nonce_consumed
from app.core.setup_nonce import verify_nonce
from app.domains.role.service import find_role_by_code_ci
from app.domains.user.model import User

FIRST_ADMIN_EMAIL = "sin9484@gmail.com"
SUPER_ADMIN_CODE = "SUPER_ADMIN"

_LOOPBACK_HOSTNAMES = ("127.0.0.1", "localhost", "::1", "[::1]")

router = APIRouter(prefix="/desktop-setup", tags=["Desktop Setup"])


# --------------------------------------------------
# 방어 계층 (Depends)
# --------------------------------------------------

def require_loopback(request: Request) -> None:

    client = request.client

    if client is None or client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="loopback 요청만 허용됩니다.",
        )


def require_desktop_mode_and_token(
    homez_desktop_token: str | None = Cookie(default=None, alias=DESKTOP_TOKEN_COOKIE_NAME),
) -> None:
    """
    `app/core/desktop_auth.py::require_desktop_token`과 달리, Desktop
    모드가 아니면 조용히 통과시키지 않는다 — 최초 설정 경로는 오직
    Desktop Shell 안에서만 허용되어야 하므로 fail-closed로 거부한다.
    """

    current = get_desktop_token()

    if current is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="이 기능은 HOMEZ Desktop 모드에서만 사용할 수 있습니다.",
        )

    if not homez_desktop_token or not constant_time_compare(homez_desktop_token, current):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Desktop 인증이 필요합니다.",
        )


def require_matching_origin(request: Request) -> None:
    """
    DNS 리바인딩 등으로 외부 Host 이름이 loopback IP로 연결되는
    경우를 방어한다 — Host 헤더 자체가 loopback 표기가 아니면 거부.
    Origin 헤더가 있으면(브라우저가 cross-origin 요청에 항상 붙임)
    Host와 정확히 일치해야 한다.
    """

    host_header = request.headers.get("host", "")
    hostname = host_header.rsplit(":", 1)[0] if ":" in host_header else host_header

    if hostname not in _LOOPBACK_HOSTNAMES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="허용되지 않는 Host입니다.",
        )

    origin_header = request.headers.get("origin")

    if origin_header:
        origin_netloc = urlparse(origin_header).netloc

        if origin_netloc != host_header:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Origin이 현재 서버와 일치하지 않습니다.",
            )


# --------------------------------------------------
# Schema
# --------------------------------------------------

class DesktopSetupStatusResponse(BaseModel):

    setup_required: bool
    configured_email: str | None = None


class FirstAdminInitializeRequest(BaseModel):

    email: str = Field(..., description="반드시 고정된 최초 관리자 이메일과 일치해야 함")
    password: str
    password_confirmation: str
    setup_nonce: str
    company_name: str = Field(..., min_length=1, max_length=100)


class FirstAdminInitializeResponse(BaseModel):

    success: bool
    user_id: int
    company_id: int


# --------------------------------------------------
# 엔드포인트
# --------------------------------------------------

@router.get("/status", response_model=DesktopSetupStatusResponse)
def get_setup_status(db: Session = Depends(get_db)):
    """
    최소 정보만 반환한다. 로그인 여부와 무관하게(로그인 화면 자체를
    보여줄지, 최초 설정 화면을 보여줄지 결정하는 데 필요하므로)
    호출 가능해야 한다 — `/console` 정적 페이지와 동일한 신뢰 수준.
    """

    user_count = db.query(User).count()
    setup_required = user_count == 0

    return DesktopSetupStatusResponse(
        setup_required=setup_required,
        configured_email=FIRST_ADMIN_EMAIL if setup_required else None,
    )


@router.post(
    "/initialize",
    response_model=FirstAdminInitializeResponse,
    dependencies=[
        Depends(require_loopback),
        Depends(require_matching_origin),
        Depends(require_desktop_mode_and_token),
    ],
)
def initialize_first_admin(
    data: FirstAdminInitializeRequest,
    db: Session = Depends(get_db),
):

    if data.email != FIRST_ADMIN_EMAIL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="허용되지 않는 이메일입니다.",
        )

    nonce_status = verify_nonce(data.setup_nonce)

    if nonce_status != NonceCheckStatus.VALID:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"setup nonce 검증 실패: {nonce_status.value}",
        )

    if data.password != data.password_confirmation:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="비밀번호 확인이 일치하지 않습니다.",
        )

    policy_errors = validate_password_policy(data.password, settings)

    if policy_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=policy_errors,
        )

    role = find_role_by_code_ci(db, SUPER_ADMIN_CODE)

    if role is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"'{SUPER_ADMIN_CODE}' 역할이 존재하지 않습니다.",
        )

    role_id = role.id

    # `db`(get_db Session)가 위 조회로 연 read Transaction을 여기서
    # 명시적으로 끝낸다 — 열어둔 채로 두면 그 SHARED 잠금이 아래
    # atomic_create_first_admin()의 별도 sqlite3 커넥션이 최종 COMMIT
    # 시 필요로 하는 배타적 쓰기 잠금과 겹쳐 "database is locked"를
    # 유발할 수 있다(2026-07-30, 원자적 생성 설계 중 식별).
    db.commit()

    company_name = data.company_name.strip()

    if not company_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="회사명을 입력해야 합니다.",
        )

    result = atomic_create_first_admin(
        resolve_sqlite_path(),
        username=FIRST_ADMIN_EMAIL,
        email=FIRST_ADMIN_EMAIL,
        password_hash=hash_password(data.password),
        role_id=role_id,
        company_name=company_name,
    )

    if result.status == FirstAdminSetupStatus.ALREADY_COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 최초 설정이 완료되었습니다.",
        )

    mark_nonce_consumed()

    return FirstAdminInitializeResponse(
        success=True, user_id=result.user_id, company_id=result.company_id,
    )


__all__ = [
    "router",
    "FIRST_ADMIN_EMAIL",
    "require_loopback",
    "require_desktop_mode_and_token",
    "require_matching_origin",
]
