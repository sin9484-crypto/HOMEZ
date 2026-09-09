"""
=========================================================
Homez OS

File : app/core/token.py
Version : 5.1.0

JWT Token Utilities

2026-08-30 V7 후속 안정화 Phase 2 — Refresh Token에 `jti`(발급 1건의
고유 식별자)를 추가했다. 이전에는 access token만 jti를 가졌고 refresh
token은 없어 서버가 개별 발급 건을 식별·소비·재사용탐지할 방법이
없었다(감사 결함 — 폐기된 세션의 refresh token도 계속 새 access
token을 발급받을 수 있었다). 실제 폐기 상태 확인·rotation·재사용
탐지는 app/domains/session/refresh_service.py가 담당한다 — 여기
`revoke_refresh_token()` 빈 스텁(항상 True만 반환, 아무 것도 하지
않음, 호출하는 곳도 전혀 없었음)은 그 진짜 구현으로 대체되었으므로
삭제한다.
=========================================================
"""

import secrets
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any

from jose import JWTError
from jose import jwt

from app.core.config import settings


def create_refresh_token(
    data: dict[str, Any],
    expires_delta: timedelta | None = None,
) -> str:
    """
    `data`에 `family_id`(app.domains.session.refresh_service가 발급한
    Refresh Token 계보 식별자)를 포함해 호출해야 서버가 이 토큰을
    추적할 수 있다 — 없으면(레거시 호출부) family_id 클레임 없이
    발급되며, 그런 토큰은 refresh_service의 family 기반 검사를 통과할
    수 없다(의도된 fail-closed).
    """

    now = datetime.now(timezone.utc)

    expire = now + (
        expires_delta
        or timedelta(
            days=settings.REFRESH_TOKEN_EXPIRE_DAYS,
        )
    )

    payload = data.copy()

    payload.update(
        {
            "exp": expire,
            "iat": now,
            "nbf": now,
            "iss": settings.TOKEN_ISSUER,
            "aud": settings.TOKEN_AUDIENCE,
            "jti": secrets.token_hex(16),
            "type": "refresh",
        }
    )

    return jwt.encode(
        payload,
        settings.SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_refresh_token(
    token: str,
) -> dict[str, Any]:

    return jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        issuer=settings.TOKEN_ISSUER,
        audience=settings.TOKEN_AUDIENCE,
    )


def verify_refresh_token(
    token: str,
) -> bool:

    try:

        payload = decode_refresh_token(
            token,
        )

        return (
            payload.get("type")
            == "refresh"
        )

    except JWTError:

        return False


def get_refresh_subject(
    token: str,
) -> str | None:

    try:

        payload = decode_refresh_token(
            token,
        )

        return payload.get("sub")

    except JWTError:

        return None


def create_token_response(
    access_token: str,
    refresh_token: str,
) -> dict[str, str]:

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


__all__ = [
    "create_refresh_token",
    "decode_refresh_token",
    "verify_refresh_token",
    "get_refresh_subject",
    "create_token_response",
]
