"""
=========================================================
Homez OS

File : app/domains/purchase_task/coupang_channel_control_provider.py

2026-10-05 — 쿠팡 판매채널 **외부 변경** 2종과 그 결과 대조용 읽기 2종의 HTTP 계약.
공식 명세(developers.coupang.com, 2026-10-05 확인)만 구현한다:

  판매중지  PUT  /v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{vendorItemId}/sales/stop
            본문 없음. 성공 {"code":"SUCCESS","message":"Sale has been suspended."}.
            400 = 삭제된 옵션·없는 옵션 등. 전제: 판매 승인 후 vendorItemId가 발급된 상태.
  주문 취소 POST /v2/providers/openapi/apis/api/v5/vendors/{vendorId}/orders/{orderId}/cancel
            본문 orderId·vendorItemIds[]·receiptCounts[]·bigCancelCode("CANERR" 고정)·
            middleCancelCode("CCTTER"=재고 연동 오류)·vendorId·userId(판매자 WING 로그인 ID).
            허용 상태는 결제완료(즉시 취소)·상품준비중(출고 중지)뿐. 한 주문번호에 박스가
            여럿이면 박스(shipmentBoxId)별로 요청해야 한다. 성공 code "200",
            data.failedItemIds. **환불과의 관계, 같은 요청을 두 번 보냈을 때의 동작은
            공식 문서에 없다** → 재요청하지 않는 구조(작업 장부)로 대응한다.
  판매 상태  GET  /v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{vendorItemId}/inventories
            data.onSale(true/false).
  발주서 1건 GET  /v2/providers/openapi/apis/api/v5/vendors/{vendorId}/ordersheets/{shipmentBoxId}
            data.status, data.orderItems[].vendorItemId/shippingCount/cancelCount/canceled.

오류 분류(쓰기 호출) — 요청이 **적용되지 않았음이 확실한지**가 기준이다:
  200 + 성공 코드            → SUCCEEDED
  200 + 명시적 실패 코드     → ACTION_REQUIRED(REJECTED), 일부 품목만 실패 → ACTION_REQUIRED(PARTIAL_FAILURE)
  200 + 해석 불가 본문       → UNKNOWN(INVALID_RESPONSE)  — 서버가 처리했을 수 있다
  429                        → RETRYABLE(적용 안 됨이 확실, Retry-After 존중)
  400/404/기타 4xx           → ACTION_REQUIRED(요청·상태·대상 오류) — 원인 해결 전 반복 금지
  401/403                    → ACTION_REQUIRED(AUTH) — 키·권한 해결 전 반복 금지
  3xx                        → ACTION_REQUIRED(REDIRECT_BLOCKED) — 따라가지 않는다
  5xx·시간초과·네트워크 오류 → UNKNOWN — 서버가 처리했을 수 있어 자동 재요청하지 않는다

자격증명(access_key·secret_key·vendor_id, 주문 취소는 wing_user_id)은 호출자가 읽어 넘기며
이 모듈은 저장·로그·오류 문구에 싣지 않는다. 응답 본문은 code/message 요약만 남긴다.
=========================================================
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import quote

import requests

from app.domains.store_connection.adapters.coupang_signing import (
    build_authorization_header,
)

BASE_URL = "https://api-gateway.coupang.com"
SALES_STOP_PATH = (
    "/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{vendor_item_id}/sales/stop"
)
INVENTORY_PATH = (
    "/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{vendor_item_id}/inventories"
)
CANCEL_PATH = "/v2/providers/openapi/apis/api/v5/vendors/{vendor_id}/orders/{order_id}/cancel"
ORDERSHEET_PATH = (
    "/v2/providers/openapi/apis/api/v5/vendors/{vendor_id}/ordersheets/{shipment_box_id}"
)

BIG_CANCEL_CODE = "CANERR"
MIDDLE_CANCEL_CODE_INVENTORY = "CCTTER"  # 재고 연동 오류(공급 불가에 해당하는 유일한 코드)

# 공식 문서가 허용하는 취소 대상 상태 중 "결제완료"(즉시 취소)만 자동 대상으로 삼는다.
# "상품준비중"은 출고 중지 요청이라 이미 포장·출고가 시작됐을 수 있어 사람이 확인한다.
CANCEL_AUTO_ALLOWED_RAW_STATUSES = frozenset({"ACCEPT"})


class CallKind:
    SUCCEEDED = "SUCCEEDED"
    RETRYABLE = "RETRYABLE"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ProviderCallResult:
    kind: str
    http_status: int | None = None
    error_class: str | None = None
    detail: str = ""
    retry_after_seconds: int | None = None


@dataclass(frozen=True)
class ProviderReadResult:
    """대조용 읽기 결과. ok=False면 값을 알 수 없다(실패를 값으로 해석하지 않는다)."""

    ok: bool
    value: bool | None = None  # SALE: onSale / ORDER: 요청 품목이 모두 취소됐는가
    detail: str = ""
    http_status: int | None = None


class ControlTransport(Protocol):
    def request(
        self, method: str, url: str, *, headers: dict[str, str],
        json_body: dict | None, timeout: float,
    ) -> requests.Response: ...


class RequestsControlTransport:
    def __init__(self, session: requests.Session | None = None):
        self._session = session or requests.Session()

    def request(
        self, method: str, url: str, *, headers: dict[str, str],
        json_body: dict | None, timeout: float,
    ) -> requests.Response:
        kwargs: dict[str, Any] = {
            "headers": headers, "timeout": timeout, "allow_redirects": False,
        }
        if json_body is not None:
            kwargs["data"] = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        return self._session.request(method, url, **kwargs)


def _body(response: requests.Response) -> dict | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _summary(body: dict | None) -> str:
    """응답의 code/message만(요청 에코·내부 식별자 제외)."""

    if not body:
        return ""
    return f"code={body.get('code')} message={str(body.get('message'))[:200]}"[:300]


def _retry_after(response: requests.Response) -> int | None:
    raw = response.headers.get("Retry-After")
    try:
        value = int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
    return value if value is not None and value >= 0 else None


class CoupangChannelControlProvider:
    """쿠팡 옵션 판매중지·주문 취소(쓰기)와 결과 대조(읽기). 자동 재시도·스케줄 없음 —
    재시도 정책은 호출하는 작업 장부 서비스의 몫이다."""

    def __init__(
        self, credential: dict[str, str], *, transport: ControlTransport | None = None,
        timeout_seconds: float = 20.0, now_factory=None,
    ):
        self._access_key = credential["access_key"]
        self._secret_key = credential["secret_key"]
        self.vendor_id = credential["vendor_id"]
        # 주문 취소 본문의 필수값. 자격증명에 없으면 추측하지 않고 호출하지 않는다.
        self._wing_user_id = (credential.get("wing_user_id") or "").strip()
        self._transport = transport or RequestsControlTransport()
        self._timeout = timeout_seconds
        self._now = now_factory or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------
    # 내부
    # ------------------------------------------------------------

    def _send(
        self, method: str, path: str, *, query: str = "", json_body: dict | None = None,
    ) -> requests.Response:
        headers = {
            "Authorization": build_authorization_header(
                self._access_key, self._secret_key, method, path, query, now=self._now(),
            ),
            "Accept": "application/json",
            "Content-Type": "application/json;charset=UTF-8",
        }
        url = f"{BASE_URL}{path}" + (f"?{query}" if query else "")
        return self._transport.request(
            method, url, headers=headers, json_body=json_body, timeout=self._timeout,
        )

    @staticmethod
    def _classify_status(response: requests.Response) -> ProviderCallResult | None:
        """성공(200)이 아닌 응답의 공통 분류. 200이면 None."""

        status = response.status_code
        if status == 200:
            return None
        detail = _summary(_body(response))
        if status == 429:
            return ProviderCallResult(
                CallKind.RETRYABLE, status, "RATE_LIMITED", detail, _retry_after(response),
            )
        if status in (401, 403):
            return ProviderCallResult(CallKind.ACTION_REQUIRED, status, "AUTH", detail)
        if 300 <= status < 400:
            return ProviderCallResult(
                CallKind.ACTION_REQUIRED, status, "REDIRECT_BLOCKED", detail,
            )
        if 400 <= status < 500:
            return ProviderCallResult(
                CallKind.ACTION_REQUIRED, status,
                "NOT_FOUND" if status == 404 else "BAD_REQUEST", detail,
            )
        return ProviderCallResult(CallKind.UNKNOWN, status, "SERVER_ERROR", detail)

    def _write(self, method: str, path: str, json_body: dict | None) -> requests.Response | ProviderCallResult:
        try:
            return self._send(method, path, json_body=json_body)
        except requests.Timeout:
            return ProviderCallResult(
                CallKind.UNKNOWN, None, "TIMEOUT", "응답 시간이 초과돼 적용 여부를 알 수 없다",
            )
        except requests.RequestException:
            return ProviderCallResult(
                CallKind.UNKNOWN, None, "NETWORK_ERROR", "네트워크 오류로 적용 여부를 알 수 없다",
            )

    # ------------------------------------------------------------
    # 쓰기
    # ------------------------------------------------------------

    def validate_stop(self, vendor_item_id: str) -> ProviderCallResult | None:
        """요청을 만들 수 없으면 사유(외부 요청 없음), 만들 수 있으면 None. 작업 장부 선점 전에
        호출해 로컬 사전검증 실패가 시도 횟수를 소모하거나 장부에 남지 않게 한다."""

        if not str(vendor_item_id or "").strip().isdigit():
            return ProviderCallResult(
                CallKind.ACTION_REQUIRED, None, "INVALID_TARGET",
                "쿠팡 옵션번호(vendorItemId)가 숫자가 아니라 요청하지 않았다",
            )
        return None

    def validate_cancel(
        self, order_id: str, vendor_item_ids: list[str], receipt_counts: list[int],
    ) -> ProviderCallResult | None:
        if not self._wing_user_id:
            return ProviderCallResult(
                CallKind.ACTION_REQUIRED, None, "MISSING_WING_USER_ID",
                "쿠팡 주문 취소 API가 요구하는 판매자 WING 로그인 ID(userId)가 자격증명에 없어 "
                "요청하지 않았다 — 추측해서 채우지 않는다",
            )
        order = str(order_id or "").strip()
        items = [str(v).strip() for v in vendor_item_ids]
        if (
            not order.isdigit() or not items or any(not v.isdigit() for v in items)
            or len(items) != len(receipt_counts) or any(int(c) < 1 for c in receipt_counts)
        ):
            return ProviderCallResult(
                CallKind.ACTION_REQUIRED, None, "INVALID_TARGET",
                "주문번호·옵션번호·수량이 쿠팡 요청 형식에 맞지 않아 요청하지 않았다",
            )
        return None

    def stop_vendor_item_sale(self, vendor_item_id: str) -> ProviderCallResult:
        """옵션(vendorItemId) 판매중지."""

        invalid = self.validate_stop(vendor_item_id)
        if invalid is not None:
            return invalid
        item = str(vendor_item_id).strip()
        path = SALES_STOP_PATH.format(vendor_item_id=item)
        sent = self._write("PUT", path, None)
        if isinstance(sent, ProviderCallResult):
            return sent
        failure = self._classify_status(sent)
        if failure is not None:
            return failure
        body = _body(sent)
        code = str((body or {}).get("code") or "").strip().upper()
        if code == "SUCCESS":
            return ProviderCallResult(CallKind.SUCCEEDED, 200, None, _summary(body))
        if code == "ERROR":
            return ProviderCallResult(
                CallKind.ACTION_REQUIRED, 200, "REJECTED", _summary(body),
            )
        return ProviderCallResult(
            CallKind.UNKNOWN, 200, "INVALID_RESPONSE",
            "200 응답이지만 성공·실패를 판정할 수 없는 본문이라 적용 여부를 알 수 없다",
        )

    def cancel_order_items(
        self, *, order_id: str, vendor_item_ids: list[str], receipt_counts: list[int],
    ) -> ProviderCallResult:
        """고객 주문(발주서) 품목 취소 — 호출자가 한 박스(shipmentBoxId) 단위로 나눠서 호출한다."""

        invalid = self.validate_cancel(order_id, vendor_item_ids, receipt_counts)
        if invalid is not None:
            return invalid
        order = str(order_id).strip()
        items = [str(v).strip() for v in vendor_item_ids]
        path = CANCEL_PATH.format(vendor_id=quote(self.vendor_id, safe=""), order_id=order)
        payload = {
            "orderId": int(order),
            "vendorItemIds": [int(v) for v in items],
            "receiptCounts": [int(c) for c in receipt_counts],
            "bigCancelCode": BIG_CANCEL_CODE,
            "middleCancelCode": MIDDLE_CANCEL_CODE_INVENTORY,
            "vendorId": self.vendor_id,
            "userId": self._wing_user_id,
        }
        sent = self._write("POST", path, payload)
        if isinstance(sent, ProviderCallResult):
            return sent
        failure = self._classify_status(sent)
        if failure is not None:
            return failure
        body = _body(sent)
        code = str((body or {}).get("code") or "").strip()
        data = (body or {}).get("data")
        if code == "200" and isinstance(data, dict):
            failed = data.get("failedItemIds") or data.get("failedVendorItemIds") or []
            if failed:
                return ProviderCallResult(
                    CallKind.ACTION_REQUIRED, 200, "PARTIAL_FAILURE",
                    f"일부 품목 취소 실패({len(failed)}건) — 쿠팡에서 주문 상태를 확인해야 한다",
                )
            return ProviderCallResult(CallKind.SUCCEEDED, 200, None, _summary(body))
        if code == "200" and data is None:
            return ProviderCallResult(
                CallKind.UNKNOWN, 200, "INVALID_RESPONSE",
                "200 응답이지만 취소 결과 본문이 없어 적용 여부를 알 수 없다",
            )
        return ProviderCallResult(
            CallKind.UNKNOWN, 200, "INVALID_RESPONSE",
            "200 응답이지만 성공·실패를 판정할 수 없는 본문이라 적용 여부를 알 수 없다",
        )

    # ------------------------------------------------------------
    # 읽기(결과불명 대조)
    # ------------------------------------------------------------

    def read_vendor_item_on_sale(self, vendor_item_id: str) -> ProviderReadResult:
        item = str(vendor_item_id or "").strip()
        if not item.isdigit():
            return ProviderReadResult(False, None, "옵션번호 형식 오류")
        path = INVENTORY_PATH.format(vendor_item_id=item)
        try:
            response = self._send("GET", path)
        except requests.RequestException:
            return ProviderReadResult(False, None, "조회 통신 오류")
        if response.status_code != 200:
            return ProviderReadResult(
                False, None, _summary(_body(response)), response.status_code,
            )
        body = _body(response) or {}
        data = body.get("data")
        on_sale = data.get("onSale") if isinstance(data, dict) else None
        if str(body.get("code")).upper() != "SUCCESS" or not isinstance(on_sale, bool):
            return ProviderReadResult(False, None, "판매 상태 응답 해석 불가", 200)
        return ProviderReadResult(True, on_sale, "", 200)

    def read_order_items_cancelled(
        self, shipment_box_id: str, vendor_item_ids: list[str],
    ) -> ProviderReadResult:
        """요청한 품목이 **모두** 취소됐으면 value=True, 하나라도 살아 있으면 False,
        응답에서 품목을 찾지 못하거나 해석할 수 없으면 ok=False."""

        box = str(shipment_box_id or "").strip()
        if not box.isdigit():
            return ProviderReadResult(False, None, "박스번호 형식 오류")
        path = ORDERSHEET_PATH.format(vendor_id=quote(self.vendor_id, safe=""), shipment_box_id=box)
        try:
            response = self._send("GET", path)
        except requests.RequestException:
            return ProviderReadResult(False, None, "조회 통신 오류")
        if response.status_code != 200:
            return ProviderReadResult(
                False, None, _summary(_body(response)), response.status_code,
            )
        body = _body(response) or {}
        data = body.get("data")
        if isinstance(data, list):
            data = data[0] if len(data) == 1 and isinstance(data[0], dict) else None
        items = data.get("orderItems") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return ProviderReadResult(False, None, "발주서 응답 해석 불가", 200)
        by_id = {str(i.get("vendorItemId")): i for i in items if isinstance(i, dict)}
        all_cancelled = True
        for wanted in vendor_item_ids:
            item = by_id.get(str(wanted))
            if item is None:
                return ProviderReadResult(False, None, "요청 품목이 발주서에 없다", 200)
            shipping = item.get("shippingCount")
            cancel = item.get("cancelCount")
            cancelled = item.get("canceled") is True or (
                isinstance(shipping, int) and isinstance(cancel, int)
                and shipping > 0 and cancel >= shipping
            )
            all_cancelled = all_cancelled and cancelled
        return ProviderReadResult(True, all_cancelled, "", 200)


__all__ = [
    "CallKind", "ProviderCallResult", "ProviderReadResult", "ControlTransport",
    "RequestsControlTransport", "CoupangChannelControlProvider",
    "CANCEL_AUTO_ALLOWED_RAW_STATUSES",
]
