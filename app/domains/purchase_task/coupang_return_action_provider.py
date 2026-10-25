"""
=========================================================
Homez OS

File : app/domains/purchase_task/coupang_return_action_provider.py

2026-10-26 — 쿠팡 쓰기 5번: **반품 입고 확인·반품 승인**(외부 변경 — 환불로 이어진다). `CoupangChannelControlProvider`에 섞어 쓴다.

공식 문서 확인(2026-10-26, developers.coupang.com):
  - 반품요청 단건 조회  GET /v2/providers/openapi/apis/api/v6/vendors/{vendorId}/returnRequests/{receiptId}
      data(배열): receiptId·orderId·receiptStatus(RELEASE_STOP_UNCHECKED·RETURNS_UNCHECKED·VENDOR_WAREHOUSE_CONFIRM·
      REQUEST_COUPANG_CHECK·RETURNS_COMPLETED)·returnItems[](vendorItemId·purchaseCount·cancelCount — 부분 반품 가능)·faultByType·
      preRefund·returnShippingCharge·completeConfirmType. 400 = 내 것이 아니거나 없음(고객이 철회했을 수 있다).
  - 반품상품 입고 확인처리  PATCH .../v4/vendors/{vendorId}/returnRequests/{receiptId}/receiveConfirmation  본문 {vendorId, receiptId}
      반품접수(RETURNS_UNCHECKED)만. 빠른환불 대상(일반 배송·10만원 미만, 신선·주문제작 제외)은 **바로 환불되고 승인·완료**된다.
  - 반품요청 승인 처리  PATCH .../v4/vendors/{vendorId}/returnRequests/{receiptId}/approval  본문 {vendorId, receiptId, cancelCount}
      입고완료(VENDOR_WAREHOUSE_CONFIRM)만. 승인하면 **환불 처리**된다. cancelCount는 접수 수량과 같아야 한다.
      400 "이미 반품이 완료 되었습니다"·"수량이 일치하지 않습니다", 500 "승인할 수 없는 상태". 성공 {"code":"200","message":"OK"}.

규칙:
  - 구매자 개인정보(요청자 이름·연락처·주소)는 읽지 않는다. 남기는 것은 번호·상태·수량·귀책·빠른환불 여부·반품배송비 금액뿐이다.
  - 응답이 예상 모양이 아니면 추측하지 않고 실패(ok=False)로 돌려준다.
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

RETURN_ONE_PATH = "/v2/providers/openapi/apis/api/v6/vendors/{vendor_id}/returnRequests/{receipt_id}"
RETURN_RECEIVE_PATH = "/v2/providers/openapi/apis/api/v4/vendors/{vendor_id}/returnRequests/{receipt_id}/receiveConfirmation"
RETURN_APPROVE_PATH = "/v2/providers/openapi/apis/api/v4/vendors/{vendor_id}/returnRequests/{receipt_id}/approval"
MAX_RECEIPT_ID_DIGITS = 20

RECEIVE = "RECEIVE"
APPROVE = "APPROVE"
# 동작마다 쿠팡이 받는 상태(공식 문서)
REQUIRED_STATUS = {RECEIVE: "RETURNS_UNCHECKED", APPROVE: "VENDOR_WAREHOUSE_CONFIRM"}
# 동작이 적용된 뒤 볼 수 있는 상태(입고 확인 뒤에는 빠른환불이면 바로 완료, 쿠팡 확인 요청이 될 수도 있다)
APPLIED_STATUSES = {
    RECEIVE: frozenset({"VENDOR_WAREHOUSE_CONFIRM", "REQUEST_COUPANG_CHECK", "RETURNS_COMPLETED"}),
    APPROVE: frozenset({"RETURNS_COMPLETED"}),
}
KNOWN_STATUSES = frozenset({"RELEASE_STOP_UNCHECKED", "RETURNS_UNCHECKED", "VENDOR_WAREHOUSE_CONFIRM", "REQUEST_COUPANG_CHECK", "RETURNS_COMPLETED"})


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None and str(value).strip().lstrip("-").isdigit() else None
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None and not isinstance(value, (dict, list)) else ""
    return text or None


def valid_receipt_id(receipt_id: Any) -> bool:
    text = str(receipt_id or "").strip()
    return not isinstance(receipt_id, bool) and text.isdigit() and len(text) <= MAX_RECEIPT_ID_DIGITS


@dataclass(frozen=True)
class ReturnSnapshot:
    """반품 1건 읽기 결과. ok=False면 아래 값은 모두 알 수 없다."""

    ok: bool
    receipt_id: str | None = None
    order_id: str | None = None
    status: str | None = None
    cancel_count: int | None = None      # returnItems[].cancelCount 합(부분 반품 가능)
    item_count: int = 0
    fault_by: str | None = None
    pre_refund: bool | None = None
    return_shipping_charge: int | None = None  # 양수 = 판매자 부담, 음수 = 고객 부담(문서)
    complete_confirm_type: str | None = None
    detail: str = ""
    http_status: int | None = None

    def to_dict(self) -> dict:
        return {
            "receipt_id": self.receipt_id, "order_id": self.order_id, "status": self.status, "cancel_count": self.cancel_count,
            "item_count": self.item_count, "fault_by": self.fault_by, "pre_refund": self.pre_refund,
            "return_shipping_charge": self.return_shipping_charge, "complete_confirm_type": self.complete_confirm_type,
        }


class _ReturnActionMixin:
    """`self._send`·`self._write`·`self._classify_status`·`self.vendor_id`가 있는 제공자에 섞는다."""

    def read_return_request(self, receipt_id: Any) -> ReturnSnapshot:
        import requests

        from app.domains.purchase_task.coupang_channel_control_provider import _body
        from app.domains.purchase_task.coupang_channel_control_provider import _summary

        if not valid_receipt_id(receipt_id):
            return ReturnSnapshot(False, detail="반품접수번호 형식 오류")
        target = str(receipt_id).strip()
        path = RETURN_ONE_PATH.format(vendor_id=quote(self.vendor_id, safe=""), receipt_id=target)
        try:
            response = self._send("GET", path)
        except requests.RequestException:
            return ReturnSnapshot(False, detail="조회 통신 오류")
        if response.status_code != 200:
            return ReturnSnapshot(False, detail=_summary(_body(response)), http_status=response.status_code)
        data = (_body(response) or {}).get("data")
        records = data if isinstance(data, list) else ([data] if isinstance(data, dict) else None)
        if not records:
            return ReturnSnapshot(False, detail="반품 응답 해석 불가", http_status=200)
        record = next((r for r in records if isinstance(r, dict) and str(_int(r.get("receiptId"))) == target), None)
        if record is None:
            return ReturnSnapshot(False, detail="응답에 요청한 반품접수번호가 없다", http_status=200)
        status = _text(record.get("receiptStatus"))
        if status not in KNOWN_STATUSES:
            return ReturnSnapshot(False, detail=f"모르는 반품 상태({status or '없음'})", http_status=200)
        raw_items = record.get("returnItems")
        items = [i for i in (raw_items if isinstance(raw_items, list) else []) if isinstance(i, dict)]
        counts = [_int(i.get("cancelCount")) for i in items]
        cancel = sum(counts) if items and all(c is not None and c > 0 for c in counts) else None
        charge = record.get("returnShippingCharge")
        units = _int(charge.get("units")) if isinstance(charge, dict) else _int(charge)
        pre = record.get("preRefund")
        return ReturnSnapshot(
            True, target, _text(record.get("orderId")), status, cancel, len(items), _text(record.get("faultByType")),
            pre if isinstance(pre, bool) else None, units, _text(record.get("completeConfirmType")), "", 200,
        )

    def validate_return_action(self, receipt_id: Any, action: str, cancel_count: Any = None):
        from app.domains.purchase_task.coupang_channel_control_provider import CallKind
        from app.domains.purchase_task.coupang_channel_control_provider import ProviderCallResult

        if not valid_receipt_id(receipt_id):
            return ProviderCallResult(CallKind.ACTION_REQUIRED, None, "INVALID_TARGET", "반품접수번호(receiptId)가 숫자가 아니라 요청하지 않았다")
        if action not in REQUIRED_STATUS:
            return ProviderCallResult(CallKind.ACTION_REQUIRED, None, "INVALID_ACTION", "알 수 없는 반품 처리라 요청하지 않았다")
        if action == APPROVE and (isinstance(cancel_count, bool) or not isinstance(cancel_count, int) or cancel_count < 1):
            return ProviderCallResult(CallKind.ACTION_REQUIRED, None, "INVALID_COUNT", "반품 승인 수량(cancelCount)이 1 이상의 정수가 아니라 요청하지 않았다")
        return None

    def process_return(self, receipt_id: Any, action: str, cancel_count: int | None = None):
        from app.domains.purchase_task.coupang_channel_control_provider import CallKind
        from app.domains.purchase_task.coupang_channel_control_provider import ProviderCallResult
        from app.domains.purchase_task.coupang_channel_control_provider import _body
        from app.domains.purchase_task.coupang_channel_control_provider import _summary

        invalid = self.validate_return_action(receipt_id, action, cancel_count)
        if invalid is not None:
            return invalid
        target = str(receipt_id).strip()
        template = RETURN_RECEIVE_PATH if action == RECEIVE else RETURN_APPROVE_PATH
        path = template.format(vendor_id=quote(self.vendor_id, safe=""), receipt_id=target)
        payload: dict = {"vendorId": self.vendor_id, "receiptId": int(target)}  # 문서: receiptId는 number
        if action == APPROVE:
            payload["cancelCount"] = cancel_count
        sent = self._write("PATCH", path, payload)
        if isinstance(sent, ProviderCallResult):
            return sent
        failure = self._classify_status(sent)
        if failure is not None:
            return failure
        body = _body(sent) or {}
        code = str(body.get("code") or "").strip().upper()
        if code in ("200", "SUCCESS"):
            return ProviderCallResult(CallKind.SUCCEEDED, 200, None, _summary(body))
        if code == "ERROR":
            return ProviderCallResult(CallKind.ACTION_REQUIRED, 200, "REJECTED", _summary(body))
        return ProviderCallResult(
            CallKind.UNKNOWN, 200, "INVALID_RESPONSE", "200 응답이지만 성공·실패를 판정할 수 없는 본문이라 적용 여부를 알 수 없다",
        )

    def read_return_action_applied(self, receipt_id: Any, action: str):
        """결과불명 대조 — 적용 뒤 상태면 True, 아직 처리 전 상태면 False(안 받음), 그 밖(쿠팡 확인 요청 등)은 판단하지 않는다."""

        from app.domains.purchase_task.coupang_channel_control_provider import ProviderReadResult

        snapshot = self.read_return_request(receipt_id)
        if not snapshot.ok or action not in REQUIRED_STATUS:
            return ProviderReadResult(False, None, snapshot.detail or "조건 오류", snapshot.http_status)
        if snapshot.status in APPLIED_STATUSES[action]:
            return ProviderReadResult(True, True, "", 200)
        if snapshot.status == REQUIRED_STATUS[action]:
            return ProviderReadResult(True, False, "", 200)
        return ProviderReadResult(False, None, f"반품 상태가 {snapshot.status}라 판단하지 않는다 — 윙에서 확인 필요", 200)


__all__ = [
    "_ReturnActionMixin", "ReturnSnapshot", "RECEIVE", "APPROVE", "REQUIRED_STATUS", "APPLIED_STATUSES", "KNOWN_STATUSES",
    "RETURN_ONE_PATH", "RETURN_RECEIVE_PATH", "RETURN_APPROVE_PATH", "valid_receipt_id",
]
