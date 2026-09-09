"""Read-only Coupang outbound/return location lookup and verified cache."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from app.domains.store_connection.adapters.coupang_signing import build_authorization_header

BASE_URL = "https://api-gateway.coupang.com"
OUTBOUND_PATH = "/v2/providers/marketplace_openapi/apis/api/v2/vendor/shipping-place/outbound"
RETURN_PATH = "/v2/providers/openapi/apis/api/v5/vendors/{vendor_id}/returnShippingCenters"
MAX_PAGES = 100
CACHE_TTL_SECONDS = 900


class CoupangLogisticsProviderError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise CoupangLogisticsProviderError("쿠팡 물류 조회의 redirect를 차단했습니다.")


@dataclass(frozen=True)
class LogisticsLocation:
    code: str
    name: str
    usable: bool
    contact_number: str = ""
    zip_code: str = ""
    address: str = ""
    address_detail: str = ""

    def public_dict(self, code_key: str) -> dict:
        return {code_key: self.code, "shipping_place_name": self.name, "usable": self.usable}


class CoupangLogisticsProvider:
    def __init__(self, credential: dict, timeout: int = 10, sleep=time.sleep):
        self.vendor_id = str(credential.get("vendor_id") or "")
        self.access_key = str(credential.get("access_key") or "")
        self.secret_key = str(credential.get("secret_key") or "")
        if not self.vendor_id or not self.access_key or not self.secret_key:
            raise CoupangLogisticsProviderError("쿠팡 API 자격증명이 준비되지 않았습니다.")
        self.timeout = timeout
        self._sleep = sleep
        self._opener = urllib.request.build_opener(_NoRedirect)

    def _request(self, path: str, params: dict) -> dict:
        query = urllib.parse.urlencode(params)
        for attempt in range(2):
            auth = build_authorization_header(
                self.access_key, self.secret_key, "GET", path, query,
                now=datetime.now(timezone.utc),
            )
            req = urllib.request.Request(
                f"{BASE_URL}{path}?{query}", method="GET",
                headers={"Authorization": auth, "Accept": "application/json"},
            )
            try:
                with self._opener.open(req, timeout=self.timeout) as response:  # noqa: S310
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt == 0:
                    retry_after = exc.headers.get("Retry-After", "1")
                    try:
                        delay = min(max(float(retry_after), 0.0), 5.0)
                    except ValueError:
                        delay = 1.0
                    self._sleep(delay)
                    continue
                label = "요청 한도 초과" if exc.code == 429 else "인증 또는 서버 오류"
                raise CoupangLogisticsProviderError(
                    f"쿠팡 물류 조회 실패: {label} (HTTP {exc.code}).", exc.code,
                ) from exc
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                raise CoupangLogisticsProviderError("쿠팡 물류 조회 서비스에 연결하지 못했습니다.") from exc
        raise CoupangLogisticsProviderError("쿠팡 물류 조회 요청 한도를 초과했습니다.", 429)

    @staticmethod
    def _address(item: dict) -> dict:
        addresses = item.get("placeAddresses") or item.get("placeAddress") or []
        return addresses[0] if addresses else item

    def list_outbound_shipping_places(self) -> list[LogisticsLocation]:
        result: list[LogisticsLocation] = []
        for page in range(1, MAX_PAGES + 1):
            data = self._request(OUTBOUND_PATH, {"pageNum": page, "pageSize": 50})
            rows = data.get("content") or (data.get("data") or {}).get("content") or []
            for item in rows:
                addr = self._address(item)
                result.append(LogisticsLocation(
                    code=str(item.get("outboundShippingPlaceCode") or ""),
                    name=str(item.get("shippingPlaceName") or ""),
                    usable=bool(item.get("usable", False)),
                    contact_number=str(addr.get("companyContactNumber") or ""),
                    zip_code=str(addr.get("returnZipCode") or ""),
                    address=str(addr.get("returnAddress") or ""),
                    address_detail=str(addr.get("returnAddressDetail") or ""),
                ))
            pagination = data.get("pagination") or (data.get("data") or {}).get("pagination") or {}
            total = int(pagination.get("totalPages") or 1)
            if page >= total or not rows:
                break
        return [item for item in result if item.code]

    def list_return_shipping_centers(self) -> list[LogisticsLocation]:
        result: list[LogisticsLocation] = []
        path = RETURN_PATH.format(vendor_id=urllib.parse.quote(self.vendor_id, safe=""))
        for page in range(1, MAX_PAGES + 1):
            data = self._request(path, {"pageNum": page, "pageSize": 50})
            response_data = data.get("data")
            if isinstance(response_data, list):
                # The official v5 contract returns the locations directly in data.
                rows = response_data
                pagination = {}
            else:
                body = response_data if isinstance(response_data, dict) else data
                rows = body.get("content") or body.get("returnShippingCenters") or []
                pagination = body.get("pagination") or {}
            for item in rows:
                addr = self._address(item)
                result.append(LogisticsLocation(
                    code=str(item.get("returnCenterCode") or ""),
                    name=str(item.get("shippingPlaceName") or item.get("returnChargeName") or ""),
                    usable=bool(item.get("usable", item.get("goodsflowStatus") != "STOPPED")),
                    contact_number=str(addr.get("companyContactNumber") or item.get("companyContactNumber") or ""),
                    zip_code=str(addr.get("returnZipCode") or item.get("returnZipCode") or ""),
                    address=str(addr.get("returnAddress") or item.get("returnAddress") or ""),
                    address_detail=str(addr.get("returnAddressDetail") or item.get("returnAddressDetail") or ""),
                ))
            total = int(pagination.get("totalPages") or 1)
            if page >= total or not rows:
                break
        return [item for item in result if item.code]


_cache_lock = threading.Lock()
_cache: dict[tuple[int, int, str], tuple[float, dict[str, LogisticsLocation]]] = {}


def cache_locations(company_id: int, wizard_id: int, kind: str, items: list[LogisticsLocation]) -> None:
    with _cache_lock:
        _cache[(company_id, wizard_id, kind)] = (time.monotonic(), {item.code: item for item in items})


def get_cached_location(company_id: int, wizard_id: int, kind: str, code: str) -> LogisticsLocation | None:
    with _cache_lock:
        entry = _cache.get((company_id, wizard_id, kind))
        if entry is None or time.monotonic() - entry[0] > CACHE_TTL_SECONDS:
            _cache.pop((company_id, wizard_id, kind), None)
            return None
        return entry[1].get(str(code))
