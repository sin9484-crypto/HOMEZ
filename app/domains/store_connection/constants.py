"""
=========================================================
Homez OS

File : app/domains/store_connection/constants.py

판매채널 연결(StoreConnection) — 고정 상수.

Secret(Access Key/Secret Key/Client Secret)은 이 Domain 어디에도
평문으로 저장하지 않는다 — Windows Credential Manager에만 저장하고
DB에는 credential_reference(target name)만 남긴다
(app/core/windows_credential_store.py 참고).
=========================================================
"""


class MarketplaceCode:
    """
    이번 Phase에서 지원하는 채널만 포함한다 — 임의 채널 추가 금지.
    """

    COUPANG = "COUPANG"
    NAVER_SMARTSTORE = "NAVER_SMARTSTORE"

    ALL = (COUPANG, NAVER_SMARTSTORE)


class ConnectionStatus:
    """
    클라이언트가 CONNECTED를 직접 지정할 수 없다 — fixture 연결 검증을
    서비스가 직접 수행한 뒤에만 CONNECTED로 전이한다
    (app/domains/store_connection/service.py::create/rotate_credential).
    """

    NOT_CONFIGURED = "NOT_CONFIGURED"
    VERIFYING = "VERIFYING"
    CONNECTED = "CONNECTED"
    ERROR = "ERROR"
    DISABLED = "DISABLED"

    ALL = (NOT_CONFIGURED, VERIFYING, CONNECTED, ERROR, DISABLED)


class ConnectionErrorCode:
    """
    fixture adapter가 반환하는 정규화된 오류 코드 — 실제 플랫폼 응답
    원문·서명값·토큰은 어디에도 포함하지 않는다
    (adapters/base.py::normalize_error 참고).
    """

    NOT_ISSUED_YET = "NOT_ISSUED_YET"
    INVALID_IDENTIFIER_FORMAT = "INVALID_IDENTIFIER_FORMAT"
    SECRET_MISMATCH = "SECRET_MISMATCH"
    APPLICATION_INACTIVE = "APPLICATION_INACTIVE"
    PERMISSION_PENDING = "PERMISSION_PENDING"
    IP_NOT_ALLOWED = "IP_NOT_ALLOWED"
    CREDENTIAL_EXPIRED = "CREDENTIAL_EXPIRED"
    ACCOUNT_IDENTIFIER_INVALID = "ACCOUNT_IDENTIFIER_INVALID"
    TOKEN_ISSUANCE_FAILED = "TOKEN_ISSUANCE_FAILED"
    UNAUTHORIZED = "UNAUTHORIZED_401"
    FORBIDDEN = "FORBIDDEN_403"
    BAD_REQUEST = "BAD_REQUEST_400"
    PRECONDITION_FAILED = "PRECONDITION_FAILED_412"
    RATE_LIMITED = "RATE_LIMITED_429"
    PLATFORM_ERROR = "PLATFORM_ERROR_5XX"
    TIMEOUT = "TIMEOUT"
    UNKNOWN = "UNKNOWN"

    ALL = (
        NOT_ISSUED_YET, INVALID_IDENTIFIER_FORMAT, SECRET_MISMATCH,
        APPLICATION_INACTIVE, PERMISSION_PENDING, IP_NOT_ALLOWED,
        CREDENTIAL_EXPIRED, ACCOUNT_IDENTIFIER_INVALID,
        TOKEN_ISSUANCE_FAILED, UNAUTHORIZED, FORBIDDEN, BAD_REQUEST,
        PRECONDITION_FAILED, RATE_LIMITED, PLATFORM_ERROR, TIMEOUT, UNKNOWN,
    )


__all__ = [
    "MarketplaceCode",
    "ConnectionStatus",
    "ConnectionErrorCode",
]
