"""Read-only Coupang purchase-order collection provider.

This module owns the HTTP contract only. It does not persist orders, advance a
cursor, acknowledge an order, or expose response bodies in error messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from urllib.parse import quote
from urllib.parse import urlencode

import requests

from app.domains.store_connection.adapters.coupang_signing import (
    build_authorization_header,
)


BASE_URL = "https://api-gateway.coupang.com"
ORDER_SHEETS_PATH = (
    "/v2/providers/openapi/apis/api/v5/vendors/{vendor_id}/ordersheets"
)
ALLOWED_STATUSES = frozenset({
    "ACCEPT", "INSTRUCT", "DEPARTURE", "DELIVERING",
    "FINAL_DELIVERY", "NONE_TRACKING",
})
MAX_WINDOW = timedelta(hours=24)
# 2026-09-17 Phase 7A 사후 감사 2차(Phase 5) — 이 값은 "평소 정상
# 페이지 수"가 아니라 nextToken이 계속 반환되는 비정상 상황(Provider
# 버그·응답 변조 등)에서 무한루프를 막는 안전 상한이다. 정상적인
# 5분 tick·ACCEPT 단일 상태 조회에서는 사실상 도달하지 않는다.
# 쿠팡 공식 문서에서 호출 빈도 제한의 구체 수치를 확인하지 못했으므로
# (근거 없이) 임의로 줄이지 않았다 — 대신 dry-run 계획(`auto_
# collection_scheduler.py::plan_manual_trigger()`)이 이 값을 그대로
# 읽어 "최대 GET 횟수"에 정직하게 반영한다(생성자 인자로 계속 override
# 가능한 설정값이며, 낮출 근거가 생기면 이 상수만 바꾸면 된다).
DEFAULT_MAX_PAGES = 100


@dataclass(frozen=True)
class CoupangOrderPage:
    orders: tuple[dict[str, Any], ...] = field(repr=False)
    next_token: str | None


@dataclass(frozen=True)
class CoupangOrderCollectionResult:
    success: bool
    pages: tuple[CoupangOrderPage, ...] = ()
    error_code: str | None = None
    error_summary: str | None = None
    retry_after_seconds: int | None = None
    http_status: int | None = None
    # 2026-09-17 개인 베타 실데이터 검증 Phase 6 후속 — 쿠팡 응답이
    # 실패했을 때 code/message만 안전하게 보존한다(Authorization
    # 헤더·access/secret key·vendor_id·요청 원문은 절대 포함하지
    # 않는다). 이전에는 상태코드만 정규화하고 실제 응답 바디를 전부
    # 버려, "왜" 400이 났는지 알 수 없었다(실제 원인 조사 중 발견).
    provider_error_detail: str | None = None


class OrderHttpTransport(Protocol):
    def get(
        self, url: str, *, headers: dict[str, str], timeout: float,
    ) -> requests.Response: ...


class RequestsOrderHttpTransport:
    def __init__(self, session: requests.Session | None = None):
        self._session = session or requests.Session()

    def get(
        self, url: str, *, headers: dict[str, str], timeout: float,
    ) -> requests.Response:
        return self._session.get(
            url, headers=headers, timeout=timeout, allow_redirects=False,
        )


class CoupangOrderCollectionProvider:
    """Fetch Coupang order sheets without changing their channel state."""

    def __init__(
        self,
        credential: dict[str, str],
        *,
        transport: OrderHttpTransport | None = None,
        timeout_seconds: float = 20.0,
        max_pages: int = DEFAULT_MAX_PAGES,
        now_factory=None,
    ):
        self._access_key = credential["access_key"]
        self._secret_key = credential["secret_key"]
        self.vendor_id = credential["vendor_id"]
        self._transport = transport or RequestsOrderHttpTransport()
        self._timeout = timeout_seconds
        self._max_pages = max_pages
        self._now = now_factory or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _format_kst(value: datetime) -> str:
        if value.tzinfo is None:
            raise ValueError("ORDER_COLLECTION_TIMEZONE_REQUIRED")
        kst = timezone(timedelta(hours=9))
        return value.astimezone(kst).isoformat(timespec="minutes")

    @staticmethod
    def _retry_after(response: requests.Response) -> int | None:
        raw = response.headers.get("Retry-After")
        if raw is None:
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None

    @staticmethod
    def _error(status: int) -> tuple[str, str]:
        if status == 400:
            return "BAD_REQUEST", "쿠팡 주문 조회 조건이 올바르지 않습니다."
        if status == 401:
            return "UNAUTHORIZED", "쿠팡 주문 조회 인증에 실패했습니다."
        if status == 403:
            return "FORBIDDEN", "쿠팡 주문 조회 권한이 없습니다."
        if status == 404:
            return "NOT_FOUND", "쿠팡 주문 조회 경로를 찾을 수 없습니다."
        if status == 412:
            return "PRECONDITION_FAILED", "쿠팡 주문 조회의 사전 조건을 충족하지 못했습니다."
        if status == 429:
            return "RATE_LIMITED", "쿠팡 주문 조회 요청이 너무 많습니다."
        if status >= 500:
            return "PLATFORM_ERROR", "쿠팡 주문 조회 서비스에 오류가 발생했습니다."
        if 300 <= status < 400:
            return "REDIRECT_BLOCKED", "쿠팡 주문 조회의 리다이렉트를 차단했습니다."
        return "HTTP_ERROR", "쿠팡 주문 조회에 실패했습니다."

    @staticmethod
    def _provider_error_detail(response: requests.Response) -> str | None:
        """실패 응답 바디에서 `code`/`message`만 뽑아 남긴다 — 그
        외 필드(요청 에코, 내부 식별자 등 무엇이 들어있을지 확신할
        수 없는 값)는 포함하지 않는다. Authorization 헤더나 요청에
        쓰인 access/secret key·vendor_id는애초에 응답 바디에 없으므로
        이 함수가 볼 수도 없다."""

        try:
            body = response.json()
        except ValueError:
            return None
        if not isinstance(body, dict):
            return None
        code = body.get("code")
        message = body.get("message")
        if code is None and message is None:
            return None
        return f"code={code} message={str(message)[:300] if message else None}"[:400]

    @staticmethod
    def _parse_page(payload: Any) -> CoupangOrderPage:
        if not isinstance(payload, dict):
            raise ValueError("INVALID_RESPONSE")
        code = payload.get("code")
        if code not in (200, "200", "SUCCESS"):
            raise ValueError("PROVIDER_REJECTED")
        data = payload.get("data")
        if data is None:
            data = []
        if not isinstance(data, list) or any(not isinstance(x, dict) for x in data):
            raise ValueError("INVALID_RESPONSE")
        next_token = payload.get("nextToken")
        if next_token is not None:
            next_token = str(next_token).strip() or None
        return CoupangOrderPage(tuple(data), next_token)

    def collect(
        self,
        *,
        created_at_from: datetime,
        created_at_to: datetime,
        status: str,
    ) -> CoupangOrderCollectionResult:
        if created_at_from.tzinfo is None or created_at_to.tzinfo is None:
            return CoupangOrderCollectionResult(
                False, error_code="TIMEZONE_REQUIRED",
                error_summary="주문 조회 시각에는 시간대가 필요합니다.",
            )
        if created_at_to <= created_at_from:
            return CoupangOrderCollectionResult(
                False, error_code="INVALID_TIME_RANGE",
                error_summary="주문 조회 종료 시각은 시작 시각보다 늦어야 합니다.",
            )
        # 2026-09-17 개인 베타 실데이터 검증 Phase 6 — 실제 쿠팡 API로
        # 정확히 24시간 구간을 조회했더니 400을 받았다(공식 문서:
        # "An error occurs when searching for more than 24 hours" —
        # 실제 서버는 24시간과 "같음"도 거부하는 것으로 확인됨). 이전
        # 코드는 `>` 비교라 정확히 24시간(=MAX_WINDOW)인 구간을
        # 통과시켰다 — 서버 쪽 제약보다 느슨했다. `>=`로 고쳐 반드시
        # 24시간보다 짧은 구간만 통과시킨다.
        if created_at_to - created_at_from >= MAX_WINDOW:
            return CoupangOrderCollectionResult(
                False, error_code="TIME_RANGE_TOO_LARGE",
                error_summary="분단위 주문 조회 범위는 24시간보다 짧아야 합니다.",
            )
        if status not in ALLOWED_STATUSES:
            return CoupangOrderCollectionResult(
                False, error_code="INVALID_STATUS",
                error_summary="지원하지 않는 쿠팡 발주서 상태입니다.",
            )

        path = ORDER_SHEETS_PATH.format(vendor_id=quote(self.vendor_id, safe=""))
        base_query = {
            "createdAtFrom": self._format_kst(created_at_from),
            "createdAtTo": self._format_kst(created_at_to),
            "searchType": "timeFrame",
            "status": status,
        }
        pages: list[CoupangOrderPage] = []
        seen_tokens: set[str] = set()
        next_token: str | None = None

        for _ in range(self._max_pages):
            params = dict(base_query)
            if next_token is not None:
                params["nextToken"] = next_token
            query = urlencode(params)
            headers = {
                "Authorization": build_authorization_header(
                    self._access_key, self._secret_key, "GET", path, query,
                    now=self._now(),
                ),
                "Accept": "application/json",
            }
            try:
                response = self._transport.get(
                    f"{BASE_URL}{path}?{query}",
                    headers=headers,
                    timeout=self._timeout,
                )
            except requests.Timeout:
                return CoupangOrderCollectionResult(
                    False, tuple(pages), "TIMEOUT",
                    "쿠팡 주문 조회 응답 시간이 초과되었습니다.",
                )
            except requests.RequestException:
                return CoupangOrderCollectionResult(
                    False, tuple(pages), "NETWORK_ERROR",
                    "쿠팡 주문 조회 네트워크 오류가 발생했습니다.",
                )

            if response.status_code != 200:
                code, summary = self._error(response.status_code)
                return CoupangOrderCollectionResult(
                    False, tuple(pages), code, summary,
                    self._retry_after(response), response.status_code,
                    provider_error_detail=self._provider_error_detail(response),
                )
            try:
                page = self._parse_page(response.json())
            except (ValueError, TypeError):
                return CoupangOrderCollectionResult(
                    False, tuple(pages), "INVALID_RESPONSE",
                    "쿠팡 주문 조회 응답 형식을 확인할 수 없습니다.",
                    http_status=200,
                )
            pages.append(page)
            next_token = page.next_token
            if next_token is None:
                return CoupangOrderCollectionResult(
                    True, tuple(pages), http_status=200,
                )
            if next_token in seen_tokens:
                return CoupangOrderCollectionResult(
                    False, tuple(pages), "PAGINATION_LOOP",
                    "쿠팡 주문 조회 페이지 토큰이 반복되었습니다.",
                    http_status=200,
                )
            seen_tokens.add(next_token)

        return CoupangOrderCollectionResult(
            False, tuple(pages), "PAGE_LIMIT_EXCEEDED",
            "쿠팡 주문 조회의 안전 페이지 한도를 초과했습니다.",
            http_status=200,
        )


__all__ = [
    "ALLOWED_STATUSES",
    "CoupangOrderCollectionProvider",
    "CoupangOrderCollectionResult",
    "CoupangOrderPage",
    "OrderHttpTransport",
]
