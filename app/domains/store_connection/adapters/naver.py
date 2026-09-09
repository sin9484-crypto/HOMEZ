"""
=========================================================
Homez OS

File : app/domains/store_connection/adapters/naver.py

네이버 커머스API센터 — fixture 전용 연결 검증 Adapter. 실제 네트워크
호출을 하지 않는다. client_id/client_secret에 아래 TRIGGER_* 명백한
가짜 값을 넣으면 해당 시나리오가 결정론적으로 재현된다(테스트 전용) —
그 외 값은 전부 정상 연결로 처리한다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta

from app.domains.store_connection.adapters.base import StoreConnectionAdapter
from app.domains.store_connection.adapters.base import VerificationResult
from app.domains.store_connection.constants import ConnectionErrorCode

_CLIENT_ID_TRIGGERS: dict[str, str] = {
    "TRIGGER_INVALID_CLIENT_ID": ConnectionErrorCode.INVALID_IDENTIFIER_FORMAT,
    "TRIGGER_APP_INACTIVE": ConnectionErrorCode.APPLICATION_INACTIVE,
    "TRIGGER_PERMISSION_PENDING": ConnectionErrorCode.PERMISSION_PENDING,
    "TRIGGER_401": ConnectionErrorCode.UNAUTHORIZED,
    "TRIGGER_403": ConnectionErrorCode.FORBIDDEN,
    "TRIGGER_429": ConnectionErrorCode.RATE_LIMITED,
    "TRIGGER_5XX": ConnectionErrorCode.PLATFORM_ERROR,
    "TRIGGER_TIMEOUT": ConnectionErrorCode.TIMEOUT,
    "TRIGGER_EXPIRED": ConnectionErrorCode.CREDENTIAL_EXPIRED,
}

_CLIENT_SECRET_TRIGGERS: dict[str, str] = {
    "TRIGGER_SECRET_MISMATCH": ConnectionErrorCode.SECRET_MISMATCH,
    "TRIGGER_TOKEN_ISSUE_FAILED": ConnectionErrorCode.TOKEN_ISSUANCE_FAILED,
}

_ACCOUNT_ID_ERROR_TRIGGER = "TRIGGER_ACCOUNT_ERROR"

_ERROR_SUMMARIES: dict[str, str] = {
    ConnectionErrorCode.INVALID_IDENTIFIER_FORMAT: "Client ID 형식이 올바르지 않습니다.",
    ConnectionErrorCode.APPLICATION_INACTIVE: "애플리케이션이 비활성 상태입니다.",
    ConnectionErrorCode.PERMISSION_PENDING: "필요한 API 권한이 아직 부여되지 않았습니다.",
    ConnectionErrorCode.UNAUTHORIZED: "인증에 실패했습니다(401).",
    ConnectionErrorCode.FORBIDDEN: "접근 권한이 없습니다(403).",
    ConnectionErrorCode.RATE_LIMITED: "요청이 너무 많습니다(429) — 잠시 후 다시 시도하세요.",
    ConnectionErrorCode.PLATFORM_ERROR: "네이버 플랫폼 오류입니다(5xx) — 잠시 후 다시 시도하세요.",
    ConnectionErrorCode.TIMEOUT: "응답 시간이 초과되었습니다.",
    ConnectionErrorCode.CREDENTIAL_EXPIRED: "Client Secret이 만료되었습니다 — 재발급이 필요합니다.",
    ConnectionErrorCode.SECRET_MISMATCH: "Client Secret이 일치하지 않습니다.",
    ConnectionErrorCode.TOKEN_ISSUANCE_FAILED: "OAuth2 토큰 발급에 실패했습니다.",
    ConnectionErrorCode.ACCOUNT_IDENTIFIER_INVALID: "판매자 식별정보(account_id/type)가 올바르지 않습니다.",
    ConnectionErrorCode.UNKNOWN: "알 수 없는 오류가 발생했습니다.",
}


class NaverConnectionAdapter(StoreConnectionAdapter):

    marketplace_code = "NAVER_SMARTSTORE"

    def validate_credential_shape(self, credential_fields: dict) -> None:

        client_id = credential_fields.get("client_id", "")
        client_secret = credential_fields.get("client_secret", "")

        if not client_id or not client_secret:
            raise ValueError("client_id/client_secret이 모두 필요합니다.")

    def verify_connection(self, credential_fields: dict) -> VerificationResult:

        self.validate_credential_shape(credential_fields)

        client_id = credential_fields["client_id"]
        client_secret = credential_fields["client_secret"]
        account_id = credential_fields.get("account_id")

        error_code = _CLIENT_ID_TRIGGERS.get(client_id)
        if error_code is None:
            error_code = _CLIENT_SECRET_TRIGGERS.get(client_secret)
        if error_code is None and account_id == _ACCOUNT_ID_ERROR_TRIGGER:
            error_code = ConnectionErrorCode.ACCOUNT_IDENTIFIER_INVALID

        expiration_status, expires_at = self.get_expiration_status(
            credential_fields,
        )

        if error_code is not None:
            return VerificationResult(
                success=False, error_code=error_code,
                error_summary=self.normalize_error(error_code),
                expiration_status=expiration_status, expires_at=expires_at,
            )

        return VerificationResult(
            success=True, error_code=None, error_summary=None,
            expiration_status=expiration_status, expires_at=expires_at,
        )

    def normalize_error(self, error_code: str) -> str:

        return _ERROR_SUMMARIES.get(
            error_code, _ERROR_SUMMARIES[ConnectionErrorCode.UNKNOWN],
        )

    def get_expiration_status(
        self, credential_fields: dict, now: datetime | None = None,
    ) -> tuple[str, datetime | None]:

        now = now or datetime.utcnow()

        if credential_fields.get("client_id") == "TRIGGER_EXPIRED":
            return "EXPIRED", now - timedelta(days=1)

        # 네이버 커머스API는 서명 방식 인증이라 fixture 환경에서 실제
        # 만료일을 조회하지 않는다 — 추측하지 않고 UNKNOWN으로 둔다.
        return "UNKNOWN", None


__all__ = [
    "NaverConnectionAdapter",
]
