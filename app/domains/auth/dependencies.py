"""
=========================================================
Homez OS

File : app/domains/auth/dependencies.py
Version : 2.1.0

Authentication Dependencies
=========================================================
"""

from fastapi import Depends
from fastapi import HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from jose import JWTError

from app.core.security import (
    decode_access_token,
)

from app.database.session import (
    get_db,
)

from app.domains.auth.service import (
    AuthService,
)

from app.domains.session.service import (
    SessionStatus,
    get_session_status,
)

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/auth/login",
)


def get_current_user(
    token: str = Depends(
        oauth2_scheme
    ),
    db: Session = Depends(
        get_db
    ),
):

    def _credentials_exception(code: str = "SESSION_EXPIRED") -> HTTPException:
        # 2026-08-03: detail/상태 코드는 기존 계약대로 유지하고, 헤더로만
        # 안정적인 코드를 추가한다(app/core/auth.py::_credentials_exception
        # 과 동일 원칙).
        return HTTPException(
            status_code=401,
            detail="Could not validate credentials.",
            headers={
                "WWW-Authenticate": "Bearer",
                "X-Auth-Error-Code": code,
            },
        )

    try:

        payload = decode_access_token(
            token
        )

        username = payload.get(
            "username"
        )

        if username is None:
            raise _credentials_exception("SESSION_EXPIRED")

    except JWTError:

        raise _credentials_exception("SESSION_EXPIRED")

    jti = payload.get("jti")

    if jti:
        # app/core/auth.py::_decode_user와 동일한 서버 측 세션 폐기/
        # 만료 검사 — /auth/logout, /auth/verify도 로그아웃된 토큰을
        # 다시 인증된 것으로 취급하지 않는다.
        session_status = get_session_status(db, jti)

        if session_status == SessionStatus.REVOKED:
            raise _credentials_exception("SESSION_REVOKED")

        if session_status == SessionStatus.EXPIRED:
            raise _credentials_exception("SESSION_EXPIRED")

    service = AuthService(db)

    user = service.get_current_user(
        username
    )

    if user is None:
        raise _credentials_exception("SESSION_REVOKED")

    return user


def get_current_active_user(
    current_user=Depends(
        get_current_user
    ),
):

    if (
        hasattr(
            current_user,
            "is_active",
        )
        and not current_user.is_active
    ):

        raise HTTPException(
            status_code=403,
            detail="Inactive user.",
            headers={"X-Auth-Error-Code": "ACCOUNT_DISABLED"},
        )

    return current_user