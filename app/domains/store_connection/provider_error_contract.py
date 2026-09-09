"""
=========================================================
Homez OS

File : app/domains/store_connection/provider_error_contract.py

2026-08-04 V6 Gate 2C: 쿠팡/네이버(그리고 향후 채널) Provider 오류를
하나의 공통 계약으로 분류한다. 이전에는 `_normalize_status_code()`,
`_RETRYABLE_ERROR_CODES`, 지수 backoff 계산이 adapters/coupang_
production.py와 adapters/naver_production.py에 각각 독립적으로
구현돼 있었다(같은 로직의 중복) — 이 파일이 그 공통 부분(HTTP 상태
→ 코드 매핑, 재시도 가능 여부, backoff 계산, Secret 제거)의 단일
출처가 된다.

`app.domains.store_connection.constants.ConnectionErrorCode`는 그대로
유지한다(UI/테스트가 이미 그 정확한 문자열 값에 의존한다) — 이 모듈은
그 위에 "사용자 메시지/재시도 정책 결정에 필요한 상위 분류"만 얹는다.
=========================================================
"""

from __future__ import annotations

from enum import Enum

from app.domains.store_connection.constants import ConnectionErrorCode


class ProviderErrorCategory(str, Enum):

    INVALID_CREDENTIAL = "INVALID_CREDENTIAL"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"


# 재시도해서 해결될 가능성이 있는 카테고리만 — Credential 오류(오타·
# 폐기된 키 등)는 몇 번을 더 불러도 절대 성공하지 않으므로 자동
# 무한 재시도를 절대 하지 않는다("Credential 오류에는 자동 무한
# 재시도 금지").
_RETRYABLE_CATEGORIES = frozenset({
    ProviderErrorCategory.RATE_LIMITED,
    ProviderErrorCategory.PROVIDER_UNAVAILABLE,
    ProviderErrorCategory.TIMEOUT,
})

_CODE_TO_CATEGORY: dict[str, ProviderErrorCategory] = {
    ConnectionErrorCode.UNAUTHORIZED: ProviderErrorCategory.INVALID_CREDENTIAL,
    ConnectionErrorCode.SECRET_MISMATCH: ProviderErrorCategory.INVALID_CREDENTIAL,
    ConnectionErrorCode.CREDENTIAL_EXPIRED: ProviderErrorCategory.INVALID_CREDENTIAL,
    ConnectionErrorCode.ACCOUNT_IDENTIFIER_INVALID: ProviderErrorCategory.INVALID_CREDENTIAL,
    ConnectionErrorCode.NOT_ISSUED_YET: ProviderErrorCategory.INVALID_CREDENTIAL,
    ConnectionErrorCode.FORBIDDEN: ProviderErrorCategory.PERMISSION_DENIED,
    ConnectionErrorCode.IP_NOT_ALLOWED: ProviderErrorCategory.PERMISSION_DENIED,
    ConnectionErrorCode.APPLICATION_INACTIVE: ProviderErrorCategory.PERMISSION_DENIED,
    ConnectionErrorCode.PERMISSION_PENDING: ProviderErrorCategory.PERMISSION_DENIED,
    ConnectionErrorCode.RATE_LIMITED: ProviderErrorCategory.RATE_LIMITED,
    ConnectionErrorCode.PLATFORM_ERROR: ProviderErrorCategory.PROVIDER_UNAVAILABLE,
    ConnectionErrorCode.TOKEN_ISSUANCE_FAILED: ProviderErrorCategory.PROVIDER_UNAVAILABLE,
    ConnectionErrorCode.TIMEOUT: ProviderErrorCategory.TIMEOUT,
    ConnectionErrorCode.BAD_REQUEST: ProviderErrorCategory.INVALID_RESPONSE,
    ConnectionErrorCode.PRECONDITION_FAILED: ProviderErrorCategory.INVALID_RESPONSE,
    ConnectionErrorCode.INVALID_IDENTIFIER_FORMAT: ProviderErrorCategory.CONFIGURATION_ERROR,
    ConnectionErrorCode.UNKNOWN: ProviderErrorCategory.INVALID_RESPONSE,
}


def classify_connection_error_code(code: str) -> ProviderErrorCategory:
    """알 수 없는 코드는 안전하게 INVALID_RESPONSE로 분류한다(재시도 금지)."""

    return _CODE_TO_CATEGORY.get(code, ProviderErrorCategory.INVALID_RESPONSE)


def is_retryable_connection_error_code(code: str) -> bool:

    return classify_connection_error_code(code) in _RETRYABLE_CATEGORIES


# --------------------------------------------------
# HTTP 상태 → ConnectionErrorCode — 이전에는 coupang_production.py와
# naver_production.py에 거의 동일한 if/elif 사슬이 각각 있었다.
# --------------------------------------------------

def normalize_http_status_to_connection_error_code(status_code: int) -> str:

    if status_code == 400:
        return ConnectionErrorCode.BAD_REQUEST
    if status_code == 401:
        return ConnectionErrorCode.UNAUTHORIZED
    if status_code == 403:
        return ConnectionErrorCode.FORBIDDEN
    if status_code == 412:
        return ConnectionErrorCode.PRECONDITION_FAILED
    if status_code == 429:
        return ConnectionErrorCode.RATE_LIMITED
    if 500 <= status_code < 600:
        return ConnectionErrorCode.PLATFORM_ERROR

    return ConnectionErrorCode.UNKNOWN


# --------------------------------------------------
# Backoff — exponential + Retry-After 헤더 우선 적용
# --------------------------------------------------

def compute_backoff_seconds(
    attempt: int, base_seconds: float, retry_after_seconds: float | None = None,
) -> float:
    """
    Provider가 Retry-After를 명시했으면 그 값을 그대로 따른다(서버가
    스스로 알려준 값이 우리의 추정 exponential backoff보다 신뢰할
    수 있다). 없으면 `base_seconds * 2**(attempt-1)` exponential
    backoff를 쓴다.
    """

    if retry_after_seconds is not None and retry_after_seconds >= 0:
        return retry_after_seconds

    return base_seconds * (2 ** (attempt - 1))


def parse_retry_after_header(value: str | None) -> float | None:
    """
    Retry-After는 초 단위 정수 또는 HTTP-date일 수 있다 — 이 도메인은
    초 단위 정수만 신뢰하고 나머지는 무시한다(추측하지 않는다, 값이
    애매하면 exponential backoff로 안전하게 폴백).
    """

    if not value:
        return None

    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None

    if seconds < 0:
        return None

    return seconds


# --------------------------------------------------
# 원문 응답에서 Secret 제거 — 예외 메시지·진단 로그에 우연히 credential
# 값이 섞여 들어가는 것을 마지막 방어선에서 막는다.
# --------------------------------------------------

def redact_secrets(text: str, secrets: list[str]) -> str:

    redacted = text

    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")

    return redacted


__all__ = [
    "ProviderErrorCategory",
    "classify_connection_error_code",
    "is_retryable_connection_error_code",
    "normalize_http_status_to_connection_error_code",
    "compute_backoff_seconds",
    "parse_retry_after_header",
    "redact_secrets",
]
