"""
=========================================================
Homez OS

File : app/domains/store_connection/adapters/naver_production.py

Gate 3(2026-08-01, HOMEZ Phase 1 이후 Gate 1~5, CTO 2차 지적 반영) —
네이버 커머스API센터 실제 연결 검증 Adapter(OAuth2 Client
Credentials).

**이 파일은 get_adapter() 레지스트리에 연결되어 있지 않다** —
coupang_production.py와 동일한 원칙(fixture만 실제 서비스 경로에서
쓰인다, Gate 4 실제 Credential 확보 전까지 어떤 경로에서도 호출되지
않는다, 이 Gate에서는 실제 네트워크 호출을 하지 않는다).

**인증 방식 확인 상태(중요)**: apicenter.commerce.naver.com,
api.commerce.naver.com 모두 이번 세션에서도 직접 fetch가 실패했다.
이 파일의 알고리즘·엔드포인트는 사용자가 제시한 스펙("공식 문서
2.83.0 기준")과, 네이버가 직접 운영하는 공식 GitHub Discussion
(commerce-api-naver/commerce-api)을 포함한 다수의 독립 2차 소스가
수렴한 내용이다 — **1차 문서 직접 확인이 아니다.** 다음 재검토에서
1차 문서 접근이 가능해지면 반드시 재확인해야 한다(잔존 위험).

확인(교차확인)된 사항:
- token endpoint: POST https://api.commerce.naver.com/external/v1/oauth2/token
- 요청 본문: application/x-www-form-urlencoded
  (grant_type=client_credentials, client_id, timestamp,
  client_secret_sign, type=SELF)
- client_secret_sign 계산: naver_signing.py 참고
- timestamp: 밀리초 epoch, 약 5분 유효
- 토큰 유효기간: 약 3시간
- 401 + 응답 코드 "GW.AUTHN"(timestamp/서명 관련 인증 오류로 알려짐)
  이면 timestamp를 다시 계산해 1회 재발급을 시도한다 — 그 외 401
  (예: 잘못된 client_id/client_secret 자체)은 재시도해도 해결되지
  않으므로 즉시 실패한다.

Secret 원문(client_secret)은 로그·예외 메시지에 남기지 않는다.
access_token이 실제로 발급되더라도 이 토큰 문자열 자체도 응답
객체·로그에 남기지 않는다(연결 성공/실패 여부만 반환).
=========================================================
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import urlencode

from app.domains.store_connection.adapters.base import (
    StoreConnectionAdapter,
    VerificationResult,
)
from app.domains.store_connection.adapters.naver_signing import (
    TOKEN_URL,
    compute_client_secret_sign,
    current_timestamp_ms,
)
from app.domains.store_connection.constants import ConnectionErrorCode
from app.domains.store_connection.provider_error_contract import (
    compute_backoff_seconds,
    is_retryable_connection_error_code,
    normalize_http_status_to_connection_error_code,
    parse_retry_after_header,
)

_DEFAULT_TIMEOUT_SECONDS = 10
_MAX_GENERAL_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 1.0
_GW_AUTHN_RETRYABLE_CODE = "GW.AUTHN"

# 2026-08-04 V6 Gate 2C: coupang_production.py와 동일하게 공통 분류로 위임.
_RETRYABLE_ERROR_CODES = tuple(
    code for code in ConnectionErrorCode.ALL
    if is_retryable_connection_error_code(code)
)

_ERROR_SUMMARIES: dict[str, str] = {
    ConnectionErrorCode.BAD_REQUEST: "요청 형식이 올바르지 않습니다(400).",
    ConnectionErrorCode.UNAUTHORIZED: "인증에 실패했습니다(401) — Client ID/Client Secret을 확인하세요.",
    ConnectionErrorCode.FORBIDDEN: "접근 권한이 없습니다(403).",
    ConnectionErrorCode.RATE_LIMITED: "요청이 너무 많습니다(429) — 잠시 후 다시 시도하세요.",
    ConnectionErrorCode.PLATFORM_ERROR: "네이버 플랫폼 오류입니다(5xx) — 잠시 후 다시 시도하세요.",
    ConnectionErrorCode.TIMEOUT: "응답 시간이 초과되었습니다.",
    ConnectionErrorCode.TOKEN_ISSUANCE_FAILED: "OAuth2 토큰 발급에 실패했습니다.",
    ConnectionErrorCode.UNKNOWN: "알 수 없는 오류가 발생했습니다.",
}


class _BlockRedirectHandler(urllib.request.HTTPRedirectHandler):
    """coupang_production.py와 동일한 원칙 — 어떤 리다이렉트도 따라가지 않는다."""

    def redirect_request(  # noqa: D102
        self, req, fp, code, msg, headers, newurl,
    ):

        raise urllib.error.HTTPError(
            req.full_url, code,
            "Naver token endpoint redirect blocked — signed request must "
            "never be replayed to a different URL.",
            headers, fp,
        )


class NaverHttpTransport:
    """실제 urllib.request 호출을 감싼다 — 테스트에서 대체 가능."""

    def __init__(self):

        self._opener = urllib.request.build_opener(_BlockRedirectHandler)

    def request(
        self, url: str, data: bytes, headers: dict, timeout: int,
    ) -> tuple[int, bytes]:

        req = urllib.request.Request(
            url, data=data, headers=headers, method="POST",
        )
        with self._opener.open(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read()


class NaverProductionAdapter(StoreConnectionAdapter):

    marketplace_code = "NAVER_SMARTSTORE"

    def __init__(
        self, transport: NaverHttpTransport | None = None,
        timeout: int = _DEFAULT_TIMEOUT_SECONDS,
    ):

        self._transport = transport or NaverHttpTransport()
        self._timeout = timeout

    def validate_credential_shape(self, credential_fields: dict) -> None:

        client_id = credential_fields.get("client_id", "")
        client_secret = credential_fields.get("client_secret", "")

        if not client_id or not client_secret:
            raise ValueError("client_id/client_secret이 모두 필요합니다.")

    def verify_connection(self, credential_fields: dict) -> VerificationResult:
        """
        token 발급 자체가 이 채널의 "최소 권한 읽기 전용" 연결 확인에
        해당한다 — 토큰만 얻고 그 이후 아무 비즈니스 API도 호출하지
        않는다(발급된 토큰 문자열은 응답에 담지 않고 폐기한다).
        """

        self.validate_credential_shape(credential_fields)

        client_id = credential_fields["client_id"]
        client_secret = credential_fields["client_secret"]

        error_code = self._issue_token_with_retry(client_id, client_secret)

        if error_code is not None:
            return VerificationResult(
                success=False, error_code=error_code,
                error_summary=self.normalize_error(error_code),
                expiration_status="UNKNOWN", expires_at=None,
            )

        return VerificationResult(
            success=True, error_code=None, error_summary=None,
            expiration_status="UNKNOWN", expires_at=None,
        )

    def _issue_token_with_retry(
        self, client_id: str, client_secret: str,
    ) -> str | None:

        gw_authn_retry_used = False
        last_error_code = ConnectionErrorCode.UNKNOWN
        retry_after_seconds: float | None = None

        for attempt in range(1, _MAX_GENERAL_ATTEMPTS + 1):
            retry_after_seconds = None
            timestamp_ms = current_timestamp_ms()
            try:
                sign = compute_client_secret_sign(
                    client_id, client_secret, timestamp_ms,
                )
            except ValueError:
                # client_secret이 bcrypt salt 형식이 아니다 — 재시도해도
                # 해결되지 않는다(값 자체가 잘못됨). 추측하지 않고
                # 인증 실패로 정규화한다.
                return ConnectionErrorCode.UNAUTHORIZED

            body_params = {
                "grant_type": "client_credentials",
                "client_id": client_id,
                "timestamp": timestamp_ms,
                "client_secret_sign": sign,
                "type": "SELF",
            }
            data = urlencode(body_params).encode("ascii")
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
            }

            try:
                status, body = self._transport.request(
                    TOKEN_URL, data, headers, self._timeout,
                )
                if 200 <= status < 300:
                    return None

                last_error_code = self._normalize_status_code(status)

                if (
                    status == 401
                    and not gw_authn_retry_used
                    and self._is_gw_authn_error(body)
                ):
                    gw_authn_retry_used = True
                    continue  # 즉시 재발급(백오프 없이 timestamp만 새로 계산)

            except urllib.error.HTTPError as exc:
                last_error_code = self._normalize_status_code(exc.code)
                # 2026-08-04 V6 Gate 2C: Provider가 Retry-After를 명시했으면
                # 우리 추정 exponential backoff보다 그 값을 우선한다.
                retry_after_seconds = parse_retry_after_header(
                    exc.headers.get("Retry-After") if exc.headers else None,
                )

                if (
                    exc.code == 401
                    and not gw_authn_retry_used
                    and self._is_gw_authn_error(exc.read())
                ):
                    gw_authn_retry_used = True
                    continue

            except TimeoutError:
                last_error_code = ConnectionErrorCode.TIMEOUT

            except urllib.error.URLError:
                last_error_code = ConnectionErrorCode.PLATFORM_ERROR

            if last_error_code not in _RETRYABLE_ERROR_CODES:
                break

            if attempt < _MAX_GENERAL_ATTEMPTS:
                time.sleep(compute_backoff_seconds(
                    attempt, _BACKOFF_BASE_SECONDS, retry_after_seconds,
                ))

        return last_error_code

    @staticmethod
    def _is_gw_authn_error(body: bytes) -> bool:
        """
        응답 본문이 GW.AUTHN 코드를 담고 있는지 안전하게 확인한다 —
        JSON이 아니거나 필드가 없으면 False(재시도하지 않는다,
        추측하지 않는다).
        """

        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return False

        code = payload.get("code") or payload.get("errorCode") or ""

        return _GW_AUTHN_RETRYABLE_CODE in str(code)

    @staticmethod
    def _normalize_status_code(status: int) -> str:
        # 2026-08-04 V6 Gate 2C: coupang_production.py와 동일한 공통
        # 매핑으로 위임(중복 제거) — 네이버는 412를 쓰지 않지만, 공통
        # 함수가 412를 지원해도 이 채널은 해당 분기를 통과할 일이 없다.
        return normalize_http_status_to_connection_error_code(status)

    def normalize_error(self, error_code: str) -> str:

        return _ERROR_SUMMARIES.get(
            error_code, _ERROR_SUMMARIES[ConnectionErrorCode.UNKNOWN],
        )

    def get_expiration_status(
        self, credential_fields: dict, now: datetime | None = None,
    ) -> tuple[str, datetime | None]:
        """
        발급된 access_token 자체를 보관/반환하지 않으므로 만료 시각을
        알 수 없다 — 추측하지 않고 UNKNOWN으로 둔다.
        """

        return "UNKNOWN", None


__all__ = [
    "NaverHttpTransport",
    "NaverProductionAdapter",
]
