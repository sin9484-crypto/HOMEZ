"""
=========================================================
Homez OS

File : app/domains/store_connection/adapters/coupang.py

쿠팡 WING Open API — fixture 전용 연결 검증 Adapter. 실제 네트워크
호출을 하지 않는다. credential_fields에 아래 TRIGGER_* 명백한 가짜
값을 넣으면 해당 시나리오가 결정론적으로 재현된다(테스트 전용) —
그 외 값은 전부 정상 연결로 처리한다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta

from app.domains.store_connection.adapters.base import StoreConnectionAdapter
from app.domains.store_connection.adapters.base import VerificationResult
from app.domains.store_connection.constants import ConnectionErrorCode

_ACCESS_KEY_TRIGGERS: dict[str, str] = {
    "TRIGGER_NOT_ISSUED": ConnectionErrorCode.NOT_ISSUED_YET,
    "TRIGGER_INVALID_FORMAT": ConnectionErrorCode.INVALID_IDENTIFIER_FORMAT,
    "TRIGGER_SECRET_MISMATCH": ConnectionErrorCode.SECRET_MISMATCH,
    "TRIGGER_IP_BLOCKED": ConnectionErrorCode.IP_NOT_ALLOWED,
    "TRIGGER_EXPIRED": ConnectionErrorCode.CREDENTIAL_EXPIRED,
    "TRIGGER_401": ConnectionErrorCode.UNAUTHORIZED,
    "TRIGGER_403": ConnectionErrorCode.FORBIDDEN,
    "TRIGGER_429": ConnectionErrorCode.RATE_LIMITED,
    "TRIGGER_5XX": ConnectionErrorCode.PLATFORM_ERROR,
    "TRIGGER_TIMEOUT": ConnectionErrorCode.TIMEOUT,
}

_VENDOR_ID_PENDING_TRIGGER = "TRIGGER_PERMISSION_PENDING"

_ERROR_SUMMARIES: dict[str, str] = {
    ConnectionErrorCode.NOT_ISSUED_YET: "아직 OPEN API 키가 발급되지 않았습니다.",
    ConnectionErrorCode.INVALID_IDENTIFIER_FORMAT: (
        "업체코드 또는 Access Key 형식이 올바르지 않습니다."
    ),
    ConnectionErrorCode.SECRET_MISMATCH: "Secret Key가 일치하지 않습니다.",
    ConnectionErrorCode.PERMISSION_PENDING: (
        "OPEN API 접근 권한이 아직 반영되지 않았습니다(최대 24시간 소요될 수 있음)."
    ),
    ConnectionErrorCode.IP_NOT_ALLOWED: "허용된 IP가 아닙니다.",
    ConnectionErrorCode.CREDENTIAL_EXPIRED: "API 키가 만료되었습니다 — 재발급이 필요합니다.",
    ConnectionErrorCode.UNAUTHORIZED: "인증에 실패했습니다(401).",
    ConnectionErrorCode.FORBIDDEN: "접근 권한이 없습니다(403).",
    ConnectionErrorCode.RATE_LIMITED: "요청이 너무 많습니다(429) — 잠시 후 다시 시도하세요.",
    ConnectionErrorCode.PLATFORM_ERROR: "쿠팡 플랫폼 오류입니다(5xx) — 잠시 후 다시 시도하세요.",
    ConnectionErrorCode.TIMEOUT: "응답 시간이 초과되었습니다.",
    ConnectionErrorCode.UNKNOWN: "알 수 없는 오류가 발생했습니다.",
}


class CoupangConnectionAdapter(StoreConnectionAdapter):

    marketplace_code = "COUPANG"

    def validate_credential_shape(self, credential_fields: dict) -> None:

        vendor_id = credential_fields.get("vendor_id", "")
        access_key = credential_fields.get("access_key", "")
        secret_key = credential_fields.get("secret_key", "")

        if not vendor_id or not access_key or not secret_key:
            raise ValueError("vendor_id/access_key/secret_key가 모두 필요합니다.")

    def verify_connection(self, credential_fields: dict) -> VerificationResult:

        self.validate_credential_shape(credential_fields)

        vendor_id = credential_fields["vendor_id"]
        access_key = credential_fields["access_key"]

        if vendor_id == _VENDOR_ID_PENDING_TRIGGER:
            error_code = ConnectionErrorCode.PERMISSION_PENDING
        else:
            error_code = _ACCESS_KEY_TRIGGERS.get(access_key)

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

        if credential_fields.get("access_key") == "TRIGGER_EXPIRED":
            return "EXPIRED", now - timedelta(days=1)

        # fixture 환경은 공식 API로부터 실제 만료일을 조회하지 않으므로
        # 그 외 경우는 전부 UNKNOWN이다(추측하지 않는다).
        return "UNKNOWN", None


__all__ = [
    "CoupangConnectionAdapter",
]
