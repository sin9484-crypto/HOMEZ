"""
=========================================================
Homez OS

File : app/domains/store_connection/adapters/coupang_production.py

Gate 3(2026-08-01, HOMEZ Phase 1 이후 Gate 1~5) — 쿠팡 WING Open API
실제 연결 검증 Adapter.

**이 파일은 app/domains/store_connection/adapters/__init__.py의
get_adapter() 레지스트리에 아직 연결되어 있지 않다** — 실제 서비스
경로(verify_new/create/rotate_credential 등)는 여전히
CoupangConnectionAdapter(fixture)만 사용한다. Gate 4에서 사용자가
HOMEZ Desktop 앱에 실제 Credential을 직접 입력하고, 그 이후 별도
승인으로 이 Adapter를 실제 서비스 경로에 연결하기 전까지는 어떤
실행 경로에서도 이 클래스가 호출되지 않는다.

서명 생성은 app/domains/store_connection/adapters/coupang_signing.py
(공식 1차 문서 확인 완료)를 그대로 사용한다.

2026-08-01 CTO 2차 지적 반영 — 엔드포인트 버전/파라미터 오류 수정:
이전 버전은 미확정이던 api/v4 경로를 그대로 썼다. 공식 1차 문서를
직접 재확인한 결과(WebFetch, 2026-08-01):

  https://developers.coupang.com/hc/en-us/articles/360033919613-Return-Cancellation-Request-List-Query

  - 엔드포인트: GET /v2/providers/openapi/apis/api/v6/vendors/{vendorId}/returnRequests
    (api/v4가 아니라 **api/v6**이 현재 공식 경로다)
  - 필수 쿼리: searchType=timeFrame
  - createdAtFrom/createdAtTo 형식: "yyyy-MM-ddTHH:mm"(분 단위)
  - status 값 예: UC(반품요청접수)/RU(출고중지요청)/CC(반품완료)/PR(쿠팡확인요청)
  - 공식 예제 URL:
    https://api-gateway.coupang.com/v2/providers/openapi/apis/api/v6/
    vendors/A00012345/returnRequests?searchType=timeFrame&
    createdAtFrom=2017-08-27T11:00&createdAtTo=2017-09-03T11:00&status=UC

이 엔드포인트를 "최소 권한의 읽기 전용 연결 확인"(Gate 4 요구사항)
용도로 재사용한다. 조회 범위를 최소화하기 위해 시간 범위를 "지금부터
5분 전까지"로 좁힌다(공식 문서상 최대 31일까지 가능하지만, 연결
확인이 목적이므로 데이터 노출을 최소화한다).

**서명 query와 실제 URL query는 반드시 같은 문자열이어야 한다** —
urllib.parse.urlencode()로 딱 한 번만 만든 query_string을 서명
계산과 실제 요청 URL 양쪽에 그대로 재사용한다(두 번 따로 만들면
인코딩 방식이 미묘하게 달라져 서명 불일치로 이어질 수 있는 버그
클래스를 원천 차단). 이 query_string은 "?" 없이(공식 문서 예제의
"query" 성분과 동일하게) 서명 메시지에 들어가고, 실제 URL에는
"?"를 붙여 사용한다.

**Redirect 차단**: 이 API가 리다이렉트를 응답하는 것은 공식 문서
어디에도 없다 — 만약 실제로 3xx가 오면(다른 host든 같은 host든)
Authorization 헤더가 실린 요청을 그대로 재전송하지 않고 즉시
실패시킨다(_BlockRedirectHandler). 서명된 자격증명이 예기치 않은
목적지로 넘어가는 것을 구조적으로 막는다.

**오류 코드 분리**: 400(BAD_REQUEST)/401(UNAUTHORIZED)/403(FORBIDDEN)/
412(PRECONDITION_FAILED)/429(RATE_LIMITED)/5xx(PLATFORM_ERROR)/
timeout을 전부 서로 다른 ConnectionErrorCode로 정규화한다. 429/5xx/
timeout만 재시도하고, 나머지는 재시도해도 해결되지 않는 오류이므로
즉시 실패시킨다. 412의 정확한 발생 조건은 공식 문서에서 확인하지
못했다 — "무엇 때문에" 발생하는지 추측하지 않고, 다른 코드와
구분해서 표시만 한다(fail-closed, 원인 단정 금지).

**여전히 미확정(추측하지 않고 남김)**: 정확한 호출 제한(rate limit)
수치는 공식 문서에서 찾지 못했다 — 429 응답에는 범용 지수 백오프만
적용한다.

Secret 원문(access_key/secret_key)은 이 파일의 어떤 로그·예외
메시지에도 절대 포함하지 않는다 — HTTP 오류 본문도 그대로 노출하지
않고 정규화된 error_code/summary만 반환한다.

실제 네트워크 호출은 이번 Gate에서 전혀 하지 않는다 — 모든 테스트는
가짜 transport로만 검증한다(tests/test_store_connection_production_
adapter.py).
=========================================================
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from app.domains.store_connection.adapters.base import (
    StoreConnectionAdapter,
    VerificationResult,
)
from app.domains.store_connection.adapters.coupang_signing import (
    build_authorization_header,
)
from app.domains.store_connection.constants import ConnectionErrorCode
from app.domains.store_connection.provider_error_contract import (
    compute_backoff_seconds,
    is_retryable_connection_error_code,
    normalize_http_status_to_connection_error_code,
    parse_retry_after_header,
)

BASE_URL = "https://api-gateway.coupang.com"
_RETURN_REQUESTS_PATH_TEMPLATE = (
    "/v2/providers/openapi/apis/api/v6/vendors/{vendor_id}/returnRequests"
)

_CONNECTIVITY_CHECK_STATUS = "UC"
_CONNECTIVITY_CHECK_WINDOW = timedelta(minutes=5)
_COUPANG_DATETIME_FORMAT = "%Y-%m-%dT%H:%M"

_DEFAULT_TIMEOUT_SECONDS = 10
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 1.0

# 2026-08-04 V6 Gate 2C: 재시도 가능 여부 판단을 provider_error_contract
# 공통 분류로 위임한다(이전에는 이 튜플을 파일마다 따로 유지했다).
_RETRYABLE_ERROR_CODES = tuple(
    code for code in ConnectionErrorCode.ALL
    if is_retryable_connection_error_code(code)
)

_ERROR_SUMMARIES: dict[str, str] = {
    ConnectionErrorCode.BAD_REQUEST: "요청 형식이 올바르지 않습니다(400).",
    ConnectionErrorCode.UNAUTHORIZED: "인증에 실패했습니다(401) — Access Key/Secret Key를 확인하세요.",
    ConnectionErrorCode.FORBIDDEN: "접근 권한이 없습니다(403) — IP 허용 목록을 확인하세요.",
    ConnectionErrorCode.PRECONDITION_FAILED: "사전 조건이 충족되지 않았습니다(412).",
    ConnectionErrorCode.RATE_LIMITED: "요청이 너무 많습니다(429) — 잠시 후 다시 시도하세요.",
    ConnectionErrorCode.PLATFORM_ERROR: "쿠팡 플랫폼 오류입니다(5xx) — 잠시 후 다시 시도하세요.",
    ConnectionErrorCode.TIMEOUT: "응답 시간이 초과되었습니다.",
    ConnectionErrorCode.UNKNOWN: "알 수 없는 오류가 발생했습니다.",
}


class _BlockRedirectHandler(urllib.request.HTTPRedirectHandler):
    """
    이 signed API는 리다이렉트를 응답하지 않는다(공식 문서에 그런
    내용이 없다) — 3xx가 오면 host가 같든 다르든 절대 따라가지 않고
    즉시 HTTPError로 실패시킨다. Authorization 헤더가 실린 요청을
    예기치 않은 목적지로 재전송하는 것을 구조적으로 막는다.
    """

    def redirect_request(  # noqa: D102
        self, req, fp, code, msg, headers, newurl,
    ):

        raise urllib.error.HTTPError(
            req.full_url, code,
            "Coupang API redirect blocked — signed request must never be "
            "replayed to a different URL.",
            headers, fp,
        )


class CoupangHttpTransport:
    """
    실제 urllib.request 호출을 감싼다 — 테스트에서 이 클래스를 대체
    (monkeypatch/서브클래스)해 실제 네트워크 없이 상태코드/예외만
    주입할 수 있다(contract test 목적, 이 Gate에서는 실제 호출 없음).
    """

    def __init__(self):

        self._opener = urllib.request.build_opener(_BlockRedirectHandler)

    def request(
        self, url: str, headers: dict, timeout: int,
    ) -> tuple[int, bytes]:

        req = urllib.request.Request(url, headers=headers, method="GET")
        with self._opener.open(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read()


class CoupangProductionAdapter(StoreConnectionAdapter):

    marketplace_code = "COUPANG"

    def __init__(
        self, transport: CoupangHttpTransport | None = None,
        timeout: int = _DEFAULT_TIMEOUT_SECONDS,
    ):

        self._transport = transport or CoupangHttpTransport()
        self._timeout = timeout

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
        secret_key = credential_fields["secret_key"]

        path = _RETURN_REQUESTS_PATH_TEMPLATE.format(vendor_id=vendor_id)
        query_string = self._build_query_string()

        error_code = self._call_with_retry(
            access_key, secret_key, path, query_string,
        )

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

    @staticmethod
    def _build_query_string(now: datetime | None = None) -> str:
        """
        서명과 실제 요청이 항상 같은 query를 쓰도록 이 메서드 하나만
        query_string을 만든다(urlencode 한 번만 호출) — 호출부가
        각자 따로 문자열을 조립하면서 인코딩이 미묘하게 갈라지는
        버그를 구조적으로 막는다.
        """

        now = now or datetime.now(timezone.utc)
        window_start = now - _CONNECTIVITY_CHECK_WINDOW

        params = {
            "searchType": "timeFrame",
            "createdAtFrom": window_start.strftime(_COUPANG_DATETIME_FORMAT),
            "createdAtTo": now.strftime(_COUPANG_DATETIME_FORMAT),
            "status": _CONNECTIVITY_CHECK_STATUS,
        }

        return urlencode(params)

    def _call_with_retry(
        self, access_key: str, secret_key: str, path: str, query_string: str,
    ) -> str | None:
        """
        성공하면 None, 실패하면 정규화된 ConnectionErrorCode 문자열을
        반환한다 — 원본 HTTP 응답 본문이나 Secret은 절대 반환/로그에
        남기지 않는다.
        """

        last_error_code = ConnectionErrorCode.UNKNOWN
        retry_after_seconds: float | None = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            now = datetime.now(timezone.utc)
            authorization = build_authorization_header(
                access_key, secret_key, "GET", path, query_string, now=now,
            )
            url = f"{BASE_URL}{path}?{query_string}"
            headers = {
                "Authorization": authorization,
                "Content-Type": "application/json;charset=UTF-8",
            }
            retry_after_seconds = None

            try:
                status, _body = self._transport.request(
                    url, headers, self._timeout,
                )
                if 200 <= status < 300:
                    return None

                last_error_code = self._normalize_status_code(status)

            except urllib.error.HTTPError as exc:
                last_error_code = self._normalize_status_code(exc.code)
                # 2026-08-04 V6 Gate 2C: Provider가 Retry-After를 명시했으면
                # 우리 추정 exponential backoff보다 그 값을 우선한다.
                retry_after_seconds = parse_retry_after_header(
                    exc.headers.get("Retry-After") if exc.headers else None,
                )

            except TimeoutError:
                last_error_code = ConnectionErrorCode.TIMEOUT

            except urllib.error.URLError:
                last_error_code = ConnectionErrorCode.PLATFORM_ERROR

            if last_error_code not in _RETRYABLE_ERROR_CODES:
                break

            if attempt < _MAX_ATTEMPTS:
                time.sleep(compute_backoff_seconds(
                    attempt, _BACKOFF_BASE_SECONDS, retry_after_seconds,
                ))

        return last_error_code

    @staticmethod
    def _normalize_status_code(status: int) -> str:
        # 2026-08-04 V6 Gate 2C: coupang/naver 두 production adapter가
        # 각자 유지하던 동일한 매핑을 provider_error_contract 공통
        # 함수로 위임한다(중복 제거).
        return normalize_http_status_to_connection_error_code(status)

    def normalize_error(self, error_code: str) -> str:

        return _ERROR_SUMMARIES.get(
            error_code, _ERROR_SUMMARIES[ConnectionErrorCode.UNKNOWN],
        )

    def get_expiration_status(
        self, credential_fields: dict, now: datetime | None = None,
    ) -> tuple[str, datetime | None]:
        """
        이 읽기 전용 연결 확인 호출은 키의 정확한 만료 시각을 반환하지
        않는다(공식 문서에는 "180일 유효" 정책만 있고, 발급 시각을 이
        Adapter가 알 방법이 없다) — 추측하지 않고 UNKNOWN으로 둔다.
        """

        return "UNKNOWN", None


__all__ = [
    "BASE_URL",
    "CoupangHttpTransport",
    "CoupangProductionAdapter",
]
