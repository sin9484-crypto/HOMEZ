"""
=========================================================
Homez OS

File : app/core/security.py
Version : 5.0.0

Security Utilities
=========================================================
"""

import hashlib
import hmac
import secrets
import string
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any

import bcrypt
from jose import JWTError
from jose import jwt

from app.core.config import settings
from app.core.token import create_refresh_token


# --------------------------------------------------
# Password
# --------------------------------------------------
#
# 2026-07-30: passlib(1.7.4)의 bcrypt 백엔드 감지 코드가 설치된
# bcrypt(5.0.0)의 `__about__.__version__` 속성을 찾다가
# AttributeError로 실패하고, 그 우회 경로에서도 자체 72바이트 초과
# 자가진단 문자열을 검증하다 ValueError를 던져 hash_password/
# verify_password 호출 자체가 항상 실패했다(Desktop 로그인이 실제로는
# 한 번도 성공할 수 없었던 근본 원인). 설치된 bcrypt 라이브러리
# 자체는 정상 동작함을 직접 확인했으므로(재현 스크립트로 검증),
# passlib의 CryptContext를 거치지 않고 bcrypt를 직접 호출하도록
# 변경한다 — 새 패키지 설치나 버전 변경 없이 기존 의존성 범위 안에서
# 해결한다.

_BCRYPT_MAX_PASSWORD_BYTES = 72


def _truncate_for_bcrypt(password: str) -> bytes:
    """
    bcrypt는 72바이트를 넘는 입력을 거부한다(ValueError). 기존 passlib
    구성도 동일한 한계를 가지므로 동작을 바꾸지 않되, 예외 대신 안전하게
    자르는 쪽을 택한다.
    """

    return password.encode("utf-8")[:_BCRYPT_MAX_PASSWORD_BYTES]


def hash_password(
    password: str,
) -> str:

    salt = bcrypt.gensalt(rounds=settings.PASSWORD_BCRYPT_ROUNDS)
    hashed = bcrypt.hashpw(_truncate_for_bcrypt(password), salt)

    return hashed.decode("utf-8")


def verify_password(
    plain_password: str,
    hashed_password: str,
) -> bool:

    try:
        return bcrypt.checkpw(
            _truncate_for_bcrypt(plain_password),
            hashed_password.encode("utf-8"),
        )
    except ValueError:
        # 저장된 해시가 bcrypt 형식이 아니거나 손상된 경우 — 인증
        # 실패로 처리한다(예외를 그대로 노출해 500을 만들지 않는다).
        return False


def generate_password(
    length: int = 16,
) -> str:

    alphabet = (
        string.ascii_letters
        + string.digits
        + "!@#$%^&*"
    )

    return "".join(
        secrets.choice(alphabet)
        for _ in range(length)
    )


def create_api_key(
    prefix: str = "hz",
) -> str:

    return (
        f"{prefix}_"
        f"{secrets.token_urlsafe(32)}"
    )


def create_secret_key() -> str:

    return secrets.token_urlsafe(64)
# --------------------------------------------------
# JWT Information
# --------------------------------------------------

def get_token_payload(
    token: str,
) -> dict | None:

    try:

        return decode_access_token(token)

    except JWTError:
        return None


def get_token_subject(
    token: str,
) -> str | None:

    payload = get_token_payload(token)

    if payload is None:
        return None

    return payload.get("sub")


def get_token_type(
    token: str,
) -> str | None:

    payload = get_token_payload(token)

    if payload is None:
        return None

    return payload.get("type")


def get_token_expiration(
    token: str,
):

    payload = get_token_payload(token)

    if payload is None:
        return None

    return payload.get("exp")


def get_token_issued_at(
    token: str,
):

    payload = get_token_payload(token)

    if payload is None:
        return None

    return payload.get("iat")
# --------------------------------------------------
# JWT
# --------------------------------------------------

def create_access_token(
    data: dict[str, Any],
    expires_delta: timedelta | None = None,
) -> str:

    now = datetime.now(timezone.utc)

    expire = now + (
        expires_delta
        or timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
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
            "type": "access",
        }
    )

    return jwt.encode(
        payload,
        settings.SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_access_token(
    token: str,
) -> dict[str, Any]:

    return jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        issuer=settings.TOKEN_ISSUER,
        audience=settings.TOKEN_AUDIENCE,
    )


def create_token_pair(
    data: dict[str, Any],
) -> tuple[str, str]:

    access_token = create_access_token(
        data=data,
    )

    refresh_token = create_refresh_token(
        data=data,
    )

    return (
        access_token,
        refresh_token,
    )


def is_token_expired(
    token: str,
) -> bool:

    try:

        decode_access_token(token)

        return False

    except JWTError:

        return True
# --------------------------------------------------
# HMAC
# --------------------------------------------------

def create_hmac_signature(
    message: str,
    secret: str,
) -> str:

    return hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_hmac_signature(
    message: str,
    signature: str,
    secret: str,
) -> bool:

    expected = create_hmac_signature(
        message,
        secret,
    )

    return hmac.compare_digest(
        expected,
        signature,
    )


# --------------------------------------------------
# Hash
# --------------------------------------------------

def sha256(
    value: str,
) -> str:

    return hashlib.sha256(
        value.encode("utf-8"),
    ).hexdigest()


def md5(
    value: str,
) -> str:

    return hashlib.md5(
        value.encode("utf-8"),
    ).hexdigest()


def create_secure_token(
    length: int = 32,
) -> str:

    return secrets.token_urlsafe(length)
# --------------------------------------------------
# Utility
# --------------------------------------------------

def generate_nonce(
    length: int = 32,
) -> str:

    return secrets.token_hex(length)


def generate_session_id() -> str:

    return secrets.token_urlsafe(48)


def generate_verification_code(
    digits: int = 6,
) -> str:

    minimum = 10 ** (digits - 1)
    maximum = (10**digits) - 1

    return str(
        secrets.randbelow(
            maximum - minimum + 1,
        )
        + minimum
    )


def constant_time_compare(
    value1: str,
    value2: str,
) -> bool:

    return hmac.compare_digest(
        value1,
        value2,
    )


def fingerprint(
    *values: str,
) -> str:

    data = "|".join(values)

    return hashlib.sha256(
        data.encode("utf-8"),
    ).hexdigest()


__all__ = [
    "hash_password",
    "verify_password",
    "generate_password",
    "create_api_key",
    "create_secret_key",
    "get_token_payload",
    "get_token_subject",
    "get_token_type",
    "get_token_expiration",
    "get_token_issued_at",
    "create_access_token",
    "decode_access_token",
    "create_token_pair",
    "is_token_expired",
    "create_hmac_signature",
    "verify_hmac_signature",
    "sha256",
    "md5",
    "create_secure_token",
    "generate_nonce",
    "generate_session_id",
    "generate_verification_code",
    "constant_time_compare",
    "fingerprint",
]
