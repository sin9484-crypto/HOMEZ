"""
=========================================================
Homez OS

File : app/domains/auth/router.py
Version : 1.0.0

Authentication Router
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.audit_log import log_auth_event
from app.core.auth import get_current_user
from app.core.config import settings
from app.core.dependency import get_db
from app.core.password_policy import validate_password_policy
from app.core.permission_check import get_permission_codes_for_role
from app.core.recent_auth import is_recent_auth_locked_out
from app.core.recent_auth import issue_recent_auth_token
from app.core.recent_auth import record_recent_auth_failure
from app.core.security import decode_access_token
from app.core.security import hash_password
from app.core.security import verify_password

from app.domains.user.model import User
from app.domains.user.schema import UserResponse
from app.domains.user.service import UserService

from app.domains.auth.schema import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    AuthMessage,
    RecentAuthRequest,
    RecentAuthResponse,
    RefreshRequest,
)

from app.domains.auth.service import AuthService
from app.domains.auth.service import RefreshTokenError
from app.domains.session.service import revoke_all_sessions_for_user
from app.domains.session.service import revoke_session


router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)

_oauth2_scheme_for_logout = OAuth2PasswordBearer(
    tokenUrl="/auth/login",
    auto_error=False,
)


# --------------------------------------------------
# Login
# --------------------------------------------------

@router.post(
    "/login",
    response_model=LoginResponse,
)
def login(
    data: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    2026-07-30: 이전에는 username/password를 함수 파라미터로 그대로
    받아 FastAPI가 이를 쿼리 파라미터로 취급했다 — 비밀번호가 URL
    querystring에 그대로 노출되어(브라우저 히스토리, 프록시/서버
    접근 로그, Referer 헤더에 남을 수 있음) 절대 금지 항목("비밀번호를
    URL에 저장/노출하지 않는다")을 정면으로 위반했다. Request Body
    (`LoginRequest`)로 변경한다.
    """

    service = AuthService(db)

    is_desktop = request.headers.get("X-Homez-Desktop-Client") == "1"

    try:

        return service.login(
            data.username,
            data.password,
            is_desktop=is_desktop,
            client_ip=request.client.host if request.client else None,
        )

    except PermissionError as exc:

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc) or "Inactive user",
        ) from exc

    except ValueError as exc:

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc) or "Invalid username or password.",
        ) from exc


# --------------------------------------------------
# Refresh
# --------------------------------------------------

@router.post(
    "/refresh",
    response_model=LoginResponse,
)
def refresh(
    request: RefreshRequest,
    db: Session = Depends(get_db),
):

    service = AuthService(db)

    try:

        return service.refresh(
            request.refresh_token,
        )

    except RefreshTokenError as exc:
        # 2026-08-30 V7 후속 안정화 Phase 2 — app/core/auth.py의 기존
        # X-Auth-Error-Code 관례를 그대로 재사용한다(SESSION_EXPIRED/
        # SESSION_REVOKED) — Desktop UI가 이 헤더만 보고 원인을
        # 구분한다.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc) or "Invalid refresh token.",
            headers={"X-Auth-Error-Code": exc.code},
        ) from exc

    except PermissionError as exc:

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc) or "Inactive user",
            headers={"X-Auth-Error-Code": "ACCOUNT_DISABLED"},
        ) from exc

    except ValueError as exc:

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc) or "Invalid refresh token.",
        ) from exc


# --------------------------------------------------
# Logout
# --------------------------------------------------

@router.post(
    "/logout",
    response_model=AuthMessage,
)
def logout(

    current_user: User = Depends(
        get_current_user,
    ),
    token: str | None = Depends(
        _oauth2_scheme_for_logout,
    ),
    db: Session = Depends(get_db),

):
    """
    2026-07-30: 이전에는 "Refresh Token 저장소 추가 후 구현"이라는
    stub이었다 — 아무 것도 취소하지 않고 메시지만 반환해, 로그아웃 후
    같은 토큰으로 계속 API를 호출할 수 있었다(서버 측 세션 폐기 요구
    사항 위반). `app/domains/session/**`에 새로 추가한 서버 측 세션
    테이블(auth_sessions)에 해당 토큰의 jti를 취소 기록한다 — 이후
    `get_current_user`(app/core/auth.py, app/domains/auth/
    dependencies.py 양쪽 모두)가 이 jti를 다시 보면 거부한다.
    """

    jti = None

    if token:
        try:
            payload = decode_access_token(token)
            jti = payload.get("jti")
        except JWTError:
            jti = None

    revoked = False

    if jti:
        revoked = revoke_session(db, jti, reason="user_logout")

    log_auth_event(
        "logout",
        username=current_user.username,
        user_id=current_user.id,
        reason="revoked" if revoked else "no_session_record",
    )

    return {
        "message": "Logout completed.",
    }


# --------------------------------------------------
# Verify
# --------------------------------------------------

@router.get(
    "/verify",
)
def verify(

    current_user: User = Depends(
        get_current_user,
    ),
    db: Session = Depends(get_db),

):
    """
    2026-08-14 Gate F-2: 기존 authenticated/user_id/username 필드는
    그대로 유지한다(기존 호출부 계약 불변). `user`/`permissions`는
    이번에 추가한 필드 — Desktop 콘솔이 재시작 후 Windows Credential
    Manager에서 access_token만 복원했을 때, localStorage의
    homez_console_user(원래 origin 종속 캐시라 재시작 시 사라짐)를
    다시 채우는 데 쓴다. `POST /auth/login` 응답(LoginResponse)과
    동일한 방식으로 permissions를 계산한다.
    """

    return {
        "authenticated": True,
        "user_id": current_user.id,
        "username": current_user.username,
        # 원본 ORM 객체를 그대로 반환하지 않는다(_sa_instance_state 등
        # 내부 SQLAlchemy 상태가 JSON 인코더에 그대로 흘러 들어가는 것을
        # 막기 위해 LoginResponse.user와 동일한 UserResponse로 명시
        # 변환한다).
        "user": UserResponse.model_validate(current_user),
        "permissions": sorted(
            get_permission_codes_for_role(db, current_user.role_id),
        ),
    }


# --------------------------------------------------
# 본인 비밀번호 변경
# --------------------------------------------------

@router.post(
    "/change-password",
    response_model=AuthMessage,
)
def change_password(
    data: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    비밀번호 원문·해시는 응답에 절대 포함하지 않는다. 성공 시
    본인을 포함한 모든 세션을 즉시 폐기한다(새 비밀번호로 다시
    로그인해야 한다) — "본인 비밀번호 변경 후 기존 세션 폐기".
    """

    if not verify_password(data.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="현재 비밀번호가 올바르지 않습니다.",
        )

    if data.new_password != data.new_password_confirmation:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="새 비밀번호 확인이 일치하지 않습니다.",
        )

    policy_errors = validate_password_policy(data.new_password, settings)

    if policy_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=policy_errors,
        )

    # `current_user`는 이미 이 요청의 `db` Session에 연결된 객체이므로
    # (get_current_user가 같은 Session으로 조회) 속성만 바꾸고 아래
    # 한 번의 commit()으로 audit_log와 함께 같은 Transaction에 묶는다
    # (UserService.update()를 쓰면 즉시 별도 commit이 발생해 감사
    # 로그와 분리되므로 여기서는 쓰지 않는다).
    current_user.password_hash = hash_password(data.new_password)
    db.add(current_user)

    revoke_all_sessions_for_user(db, current_user.id, reason="password_changed")

    write_audit_log(
        db,
        user_id=current_user.id,
        action="CHANGE_OWN_PASSWORD",
        entity="users",
        entity_id=str(current_user.id),
        description="User changed their own password; all sessions revoked.",
        company_id=current_user.company_id,
    )
    db.commit()

    log_auth_event(
        "change_password", username=current_user.username, user_id=current_user.id,
    )

    return {
        "message": "비밀번호가 변경되었습니다. 모든 세션이 종료되어 다시 로그인해야 합니다.",
    }


# --------------------------------------------------
# 본인 — 다른 세션 모두 폐기
# --------------------------------------------------

@router.post(
    "/sessions/revoke-all",
    response_model=AuthMessage,
)
def revoke_all_own_sessions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """비밀번호는 바꾸지 않고, 계정 탈취가 의심될 때 등 모든 세션만 폐기한다."""

    count = revoke_all_sessions_for_user(db, current_user.id, reason="user_requested_revoke_all")

    write_audit_log(
        db,
        user_id=current_user.id,
        action="REVOKE_ALL_SESSIONS",
        entity="users",
        entity_id=str(current_user.id),
        description=f"User revoked all sessions ({count} session(s)).",
        company_id=current_user.company_id,
    )
    db.commit()

    return {"message": f"{count}개 세션이 종료되었습니다. 다시 로그인해야 합니다."}


# --------------------------------------------------
# 최근 인증(recent-auth) — 회사명 변경 등 민감한 조작 직전 재확인
# --------------------------------------------------

@router.post(
    "/recent-auth",
    response_model=RecentAuthResponse,
)
def issue_recent_auth(
    data: RecentAuthRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    이미 로그인된 사용자가 현재 비밀번호를 다시 입력하면, 5분 동안만
    유효하고 정확히 1회만 쓸 수 있는 recent-auth 토큰을 발급한다.
    회사명 변경처럼 조용히 되돌리기 어려운 조작 직전에 요구한다.

    2026-08-04 CTO 반려 재보완: 이 401은 "세션이 무효하다"는 뜻이
    전혀 아니다(호출 자체가 이미 유효한 로그인 세션을 전제한다) —
    콘솔 JS의 `skipAuthHandling`에만 기대지 않고, 서버가
    `X-Auth-Error-Code: RECENT_AUTH_FAILED`를 명시해 그 자체로
    "허용된 로그아웃 코드가 아님"을 계약으로 못박는다(app/web/
    console.js의 ALLOWED_LOGOUT_CODES에 없는 코드는 401이어도
    강제 로그아웃하지 않는다 — 이 계약은 헤더만으로 이미 성립하고,
    프런트의 skipAuthHandling은 추가 방어선일 뿐이다). 남은 시도
    횟수·잠금 여부의 구체적 사유는 응답에 넣지 않는다.
    """

    if is_recent_auth_locked_out(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="비밀번호 확인 시도가 너무 많습니다. 잠시 후 다시 시도하세요.",
        )

    if not verify_password(data.current_password, current_user.password_hash):
        record_recent_auth_failure(current_user.id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="현재 비밀번호가 올바르지 않습니다.",
            headers={"X-Auth-Error-Code": "RECENT_AUTH_FAILED"},
        )

    raw_token, expires_at = issue_recent_auth_token(current_user.id)

    return RecentAuthResponse(
        recent_auth_token=raw_token,
        expires_at=expires_at.isoformat(),
    )