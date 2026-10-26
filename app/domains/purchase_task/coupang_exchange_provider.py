"""
=========================================================
Homez OS

File : app/domains/purchase_task/coupang_exchange_provider.py

2026-10-27 — 쿠팡 쓰기 8번: **교환 요청 읽기·거부·교환 회수 송장 등록**(외부 변경). `CoupangChannelControlProvider`에 섞어 쓴다.

공식 문서 확인(2026-10-27, developers.coupang.com):
  - 교환요청 목록조회  GET /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/exchangeRequests
      createdAtFrom/To(yyyy-MM-ddTHH:mm:ss, 기간 7일 미만 — "should less then 7day"), status(선택), orderId(선택), nextToken, maxPerPage(기본 10).
      data[]: exchangeId·orderId·exchangeStatus(RECEIPT|PROGRESS|SUCCESS|REJECT|CANCEL)·collectStatus(BeforeDirection 등)·deliveryStatus·faultType·
      reasonCodeText·exchangeItemDtoV1s[](orderItemName·targetItemName·quantity)·successable·rejectable·returnDeliveryDtos[](deliveryCompanyCode·deliveryInvoiceNo).
      단건 조회 API는 문서에 없다 — 목록에서 번호로 찾는다(최근 7일 안).
  - 교환요청 거부 처리  PATCH .../v4/vendors/{vendorId}/exchangeRequests/{exchangeId}/rejection
      본문 {exchangeId(string), vendorId, exchangeRejectCode: SOLDOUT(매진)|WITHDRAW(고객 철회)}. 응답 data.resultCode SUCCESS|FAIL.
  - 회수 송장 등록(교환)  POST .../return-exchange-invoices/manual — returnExchangeDeliveryType=EXCHANGE, 회수상태 BeforeDirection일 때만.
  - 만들지 않은 것: 교환상품 입고 확인(처리 가능 상태·처리 뒤 상태가 문서에 없어 결과를 확인할 수 없다),
    교환상품 송장 업로드(요청 본문 형식이 한·영 문서 모두에 없다 — 🔴 추측해서 보내지 않는다).

규칙:
  - 구매자 개인정보(exchangeAddressDtoV1·returndeliveryDestinationDto의 이름·주소·연락처·안심번호)와 자유 글(reasonEtcDetail·extraMessage)은 읽지 않는다.
  - 응답이 예상 모양이 아니면 추측하지 않고 실패(ok=False)로 돌려준다.
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import quote
from urllib.parse import urlencode

EXCHANGE_LIST_PATH = "/v2/providers/openapi/apis/api/v4/vendors/{vendor_id}/exchangeRequests"
EXCHANGE_REJECT_PATH = "/v2/providers/openapi/apis/api/v4/vendors/{vendor_id}/exchangeRequests/{exchange_id}/rejection"
EXCHANGE_STATUSES = frozenset({"RECEIPT", "PROGRESS", "SUCCESS", "REJECT", "CANCEL"})
OPEN_EXCHANGE_STATUSES = frozenset({"RECEIPT", "PROGRESS"})
REJECT_CODES = ("SOLDOUT", "WITHDRAW")
PICKUP_COLLECT_STATUS = "BeforeDirection"   # 공식: 교환 회수 송장은 회수연동전일 때만
EXCHANGE_WINDOW = timedelta(days=7) - timedelta(minutes=1)   # 공식: 7일 미만
MAX_EXCHANGE_PAGES = 5
MAX_EXCHANGE_ID_DIGITS = 20


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None and not isinstance(value, (dict, list, bool)) else ""
    return text or None


def valid_exchange_id(value: Any) -> bool:
    text = str(value or "").strip()
    return not isinstance(value, bool) and text.isdigit() and len(text) <= MAX_EXCHANGE_ID_DIGITS


def parse_exchange(item: dict) -> dict | None:
    """목록 1건을 화면·판정용 모양으로. 개인정보·자유 글은 읽지 않는다. 상태가 문서 밖이면 None(추측하지 않는다)."""

    exchange_id = _text(item.get("exchangeId"))
    status = _text(item.get("exchangeStatus"))
    if exchange_id is None or not exchange_id.isdigit() or status not in EXCHANGE_STATUSES:
        return None
    raw_items = item.get("exchangeItemDtoV1s")
    items = [
        {"order_item": _text(i.get("orderItemName")), "target_item": _text(i.get("targetItemName")),
         "quantity": i.get("quantity") if isinstance(i.get("quantity"), int) and not isinstance(i.get("quantity"), bool) else None}
        for i in (raw_items if isinstance(raw_items, list) else []) if isinstance(i, dict)
    ]
    dtos = item.get("returnDeliveryDtos")
    invoices = tuple(dict.fromkeys(
        (_text(d.get("deliveryCompanyCode")), _text(d.get("deliveryInvoiceNo")))
        for d in (dtos if isinstance(dtos, list) else []) if isinstance(d, dict) and _text(d.get("deliveryInvoiceNo"))
    ))
    return {
        "exchange_id": exchange_id, "order_id": _text(item.get("orderId")), "status": status,
        "collect_status": _text(item.get("collectStatus")), "delivery_status": _text(item.get("deliveryStatus")),
        "fault_by": _text(item.get("faultType")), "reason": _text(item.get("reasonCodeText")), "created_at": _text(item.get("createdAt")),
        "items": items, "rejectable": item.get("rejectable") is True, "successable": item.get("successable") is True,
        "return_invoices": [{"courier": c, "invoice": n} for c, n in invoices],
    }


@dataclass(frozen=True)
class ExchangeRead:
    ok: bool
    rows: tuple[dict, ...] = ()
    truncated: bool = False
    detail: str = ""
    http_status: int | None = None


@dataclass(frozen=True)
class ExchangeLookup:
    ok: bool
    found: bool = False
    row: dict | None = None
    truncated: bool = False
    detail: str = ""
    http_status: int | None = None


class _ExchangeMixin:
    """`self._send`·`self._write`·`self._classify_status`·`self.vendor_id`·`self._now()`가 있는 제공자에 섞는다."""

    def read_exchanges(self) -> ExchangeRead:
        """최근 7일(미만) 교환 요청 전체(nextToken으로 최대 5쪽). 모르는 모양은 실패로 돌려준다."""

        import requests

        from app.domains.purchase_task.coupang_after_sales_provider import KST
        from app.domains.purchase_task.coupang_channel_control_provider import _body
        from app.domains.purchase_task.coupang_channel_control_provider import _summary

        end = self._now().astimezone(KST).replace(microsecond=0)
        start = end - EXCHANGE_WINDOW
        path = EXCHANGE_LIST_PATH.format(vendor_id=quote(self.vendor_id, safe=""))
        rows: list[dict] = []
        token = ""
        for page in range(1, MAX_EXCHANGE_PAGES + 1):
            params = {"createdAtFrom": start.strftime("%Y-%m-%dT%H:%M:%S"), "createdAtTo": end.strftime("%Y-%m-%dT%H:%M:%S")}
            if token:
                params["nextToken"] = token
            try:
                response = self._send("GET", path, query=urlencode(params))
            except requests.RequestException:
                return ExchangeRead(False, tuple(rows), detail="조회 통신 오류")
            if response.status_code != 200:
                return ExchangeRead(False, tuple(rows), detail=_summary(_body(response)), http_status=response.status_code)
            body = _body(response) or {}
            data = body.get("data")
            if not isinstance(data, list):
                return ExchangeRead(False, tuple(rows), detail="교환 응답 해석 불가", http_status=200)
            rows.extend(r for r in (parse_exchange(d) for d in data if isinstance(d, dict)) if r is not None)
            token = _text(body.get("nextToken")) or ""
            if not token:
                return ExchangeRead(True, tuple(rows), False, "", 200)
        return ExchangeRead(True, tuple(rows), True, "", 200)

    def find_exchange(self, exchange_id: Any) -> ExchangeLookup:
        if not valid_exchange_id(exchange_id):
            return ExchangeLookup(False, detail="교환 접수번호 형식 오류")
        read = self.read_exchanges()
        if not read.ok:
            return ExchangeLookup(False, detail=read.detail, http_status=read.http_status)
        target = str(exchange_id).strip()
        row = next((r for r in read.rows if r["exchange_id"] == target), None)
        return ExchangeLookup(True, row is not None, row, read.truncated, "", 200)

    def validate_exchange_reject(self, exchange_id: Any, reject_code: Any):
        from app.domains.purchase_task.coupang_channel_control_provider import CallKind
        from app.domains.purchase_task.coupang_channel_control_provider import ProviderCallResult

        if not valid_exchange_id(exchange_id):
            return ProviderCallResult(CallKind.ACTION_REQUIRED, None, "INVALID_TARGET", "교환 접수번호가 숫자가 아니라 요청하지 않았다")
        if reject_code not in REJECT_CODES:
            return ProviderCallResult(CallKind.ACTION_REQUIRED, None, "INVALID_REJECT_CODE", "거부 사유는 SOLDOUT·WITHDRAW만 쓸 수 있어 요청하지 않았다")
        return None

    def reject_exchange(self, exchange_id: Any, reject_code: str):
        from app.domains.purchase_task.coupang_channel_control_provider import CallKind
        from app.domains.purchase_task.coupang_channel_control_provider import ProviderCallResult
        from app.domains.purchase_task.coupang_channel_control_provider import _body
        from app.domains.purchase_task.coupang_channel_control_provider import _summary

        invalid = self.validate_exchange_reject(exchange_id, reject_code)
        if invalid is not None:
            return invalid
        target = str(exchange_id).strip()
        path = EXCHANGE_REJECT_PATH.format(vendor_id=quote(self.vendor_id, safe=""), exchange_id=target)
        sent = self._write("PATCH", path, {"exchangeId": target, "vendorId": self.vendor_id, "exchangeRejectCode": reject_code})
        if isinstance(sent, ProviderCallResult):
            return sent
        failure = self._classify_status(sent)
        if failure is not None:
            return failure
        body = _body(sent) or {}
        data = body.get("data")
        result = str(data.get("resultCode") or "").strip().upper() if isinstance(data, dict) else ""
        if result == "SUCCESS":
            return ProviderCallResult(CallKind.SUCCEEDED, 200, None, _summary(body))
        if result == "FAIL" or str(body.get("code") or "").strip().upper() == "ERROR":
            detail = str(data.get("resultMessage"))[:200] if isinstance(data, dict) else _summary(body)
            return ProviderCallResult(CallKind.ACTION_REQUIRED, 200, "REJECTED", detail)
        return ProviderCallResult(
            CallKind.UNKNOWN, 200, "INVALID_RESPONSE", "200 응답이지만 성공·실패를 판정할 수 없는 본문이라 적용 여부를 알 수 없다",
        )

    def read_exchange_reject_applied(self, exchange_id: Any):
        """결과불명 대조 — 교환 상태가 REJECT면 True, 아직 열린 상태(접수·진행)면 False, 그 밖·못 찾음은 판단하지 않는다."""

        from app.domains.purchase_task.coupang_channel_control_provider import ProviderReadResult

        found = self.find_exchange(exchange_id)
        if not found.ok:
            return ProviderReadResult(False, None, found.detail, found.http_status)
        if not found.found:
            return ProviderReadResult(False, None, "최근 7일 교환에서 찾지 못했다 — 윙에서 확인 필요", 200)
        if found.row["status"] == "REJECT":
            return ProviderReadResult(True, True, "", 200)
        if found.row["status"] in OPEN_EXCHANGE_STATUSES:
            return ProviderReadResult(True, False, "", 200)
        return ProviderReadResult(False, None, f"교환 상태가 {found.row['status']}라 판단하지 않는다 — 윙에서 확인 필요", 200)

    def read_exchange_pickup_applied(self, exchange_id: Any, invoice_number: str):
        """결과불명 대조 — 그 회수 송장이 붙어 있으면 True, 회수연동전이고 송장이 없으면 False, 그 밖은 판단하지 않는다."""

        from app.domains.purchase_task.coupang_channel_control_provider import ProviderReadResult

        found = self.find_exchange(exchange_id)
        if not found.ok:
            return ProviderReadResult(False, None, found.detail, found.http_status)
        if not found.found:
            return ProviderReadResult(False, None, "최근 7일 교환에서 찾지 못했다 — 윙에서 확인 필요", 200)
        invoices = [i["invoice"] for i in found.row["return_invoices"]]
        if invoice_number in invoices:
            return ProviderReadResult(True, True, "", 200)
        if found.row["collect_status"] == PICKUP_COLLECT_STATUS and not invoices:
            return ProviderReadResult(True, False, "", 200)
        return ProviderReadResult(False, None, "회수 송장 상태를 판단할 수 없다 — 윙에서 확인 필요", 200)


__all__ = [
    "_ExchangeMixin", "ExchangeRead", "ExchangeLookup", "parse_exchange", "valid_exchange_id", "EXCHANGE_STATUSES",
    "OPEN_EXCHANGE_STATUSES", "REJECT_CODES", "PICKUP_COLLECT_STATUS", "EXCHANGE_LIST_PATH", "EXCHANGE_REJECT_PATH",
]
