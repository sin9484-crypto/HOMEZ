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
        if created_at_to - created_at_from > MAX_WINDOW:
            return CoupangOrderCollectionResult(
                False, error_code="TIME_RANGE_TOO_LARGE",
                error_summary="분단위 주문 조회 범위는 24시간 이내여야 합니다.",
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
