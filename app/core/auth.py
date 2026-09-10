"""
=========================================================
Homez OS

File : app/core/auth.py
Version : 5.0.0

Authentication
=========================================================
"""

from jose import JWTError

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from fastapi.security import OAuth2PasswordBearer

from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.database.session import get_db
from app.domains.session.service import SessionStatus
from app.domains.session.service import get_session_status
from app.domains.session.service import touch_session_last_seen
from app.domains.user.model import User


oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login",
)


def _credentials_exception(code: str = "SESSION_EXPIRED") -> HTTPException:

    # 2026-08-03: 기존 detail 문자열/상태 코드는 그대로 두고(기존 계약
    # 유지), 클라이언트가 "이 401은 실제로 세션이 무효하다"는 사실을
    # 안정적으로 구분할 수 있도록 헤더로만 코드를 추가한다 — 원인별로
    # SESSION_EXPIRED/SESSION_REVOKED를 구분하되, 어느 쪽이든 클라이언트
    # 관점에서는 "허용된 로그아웃 조건"이라는 점은 동일하다.
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={
            "WWW-Authenticate": "Bearer",
            "X-Auth-Error-Code": code,
        },
    )


def _decode_user(
    token: str,
    db: Session,
) -> User:

    try:

        payload = decode_access_token(token)

    except JWTError as exc:

        raise _credentials_exception("SESSION_EXPIRED") from exc

    user_id = payload.get("sub")

    if user_id is None:
        raise _credentials_exception("SESSION_EXPIRED")

    jti = payload.get("jti")

    if jti:
        # 로그아웃/세션 만료로 서버가 명시적으로 취소한 토큰은, 서명이
        # 여전히 유효하고 JWT 자체 만료 전이어도 거부한다(`auth_sessions`
        # 테이블이 아직 없는 환경에서는 NOT_FOUND/SCHEMA_NOT_READY가
        # 반환되어 기존 JWT 자연 만료 검증에만 의존하는 이전 동작으로
        # 안전하게 폴백한다 — 이 세션이 새로 도입한 기능이므로 그 이전에
        # 발급된 토큰까지 소급 차단하지 않는다).
        session_status = get_session_status(db, jti)

        if session_status == SessionStatus.REVOKED:
            raise _credentials_exception("SESSION_REVOKED")

        if session_status == SessionStatus.EXPIRED:
            raise _credentials_exception("SESSION_EXPIRED")

        # 2026-09-09 Phase 2(HOMEZ_USER_OPERATION_SETTINGS.md 11번 —
        # "마지막 사용 후 최대 3시간까지만 유지") — JWT 자체는 아직
        # 안 만료됐어도 그만큼 요청이 없었으면 여기서 거부한다.
        if session_status == SessionStatus.IDLE_TIMEOUT:
            raise _credentials_exception("SESSION_IDLE_TIMEOUT")

        # 세션이 실제로 유효했던 이번 요청을 "마지막 사용"으로 기록한다
        # (VALID일 때만 — NOT_FOUND/SCHEMA_NOT_READY는 세션 계층 자체가
        # 없거나 이 토큰을 세션 테이블이 모르는 경우라 touch할 대상이
        # 없다). 실패해도 요청 자체는 막지 않는다(관측 기능일 뿐, 그
        # 자체가 인증 판정에 관여하지 않는다).
        if session_status == SessionStatus.VALID:
            try:
                touch_session_last_seen(db, jti)
            except Exception:  # noqa: BLE001
                pass

    user = (
        db.query(User)
        .filter(User.id == int(user_id))
        .first()
    )

    if user is None:
        raise _credentials_exception("SESSION_REVOKED")

    return user


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Bearer 토큰 기준 현재 사용자."""

    return _decode_user(
        token,
        db,
    )


def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:

    if not getattr(
        current_user,
        "is_active",
        True,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
            headers={"X-Auth-Error-Code": "ACCOUNT_DISABLED"},
        )

    return current_user


def get_current_superuser(
    current_user: User = Depends(get_current_active_user),
) -> User:

    role = getattr(
        current_user,
        "role",
        "",
    )
    is_staff = getattr(
        current_user,
        "is_staff",
        False,
    )

    if not (
        is_staff
        or role in {
            "ADMIN",
            "SUPERUSER",
            "SUPER_ADMIN",
        }
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient privileges",
        )

    return current_user


def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User | None:

    return get_request_user(
        request=request,
        db=db,
    )


def get_request_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User | None:

    authorization = request.headers.get(
        "Authorization"
    )

    if not authorization:
        return None

    if not authorization.startswith(
        "Bearer "
    ):
        return None

    token = authorization[
        7:
    ].strip()

    try:

        return get_current_user(
            token=token,
            db=db,
        )

    except HTTPException:

        return None


def is_authenticated(
    request: Request,
    db: Session = Depends(get_db),
) -> bool:

    return (
        get_request_user(
            request=request,
            db=db,
        )
        is not None
    )


def require_authenticated(
    current_user: User = Depends(get_current_user),
) -> User:

    return current_user


def require_superuser(
    current_user: User = Depends(get_current_superuser),
) -> User:

    return current_user


__all__ = [
    "oauth2_scheme",
    "get_current_user",
    "get_current_active_user",
    "get_current_superuser",
    "get_optional_user",
    "get_request_user",
    "is_authenticated",
    "require_authenticated",
    "require_superuser",
]
