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
            data.status, data.orderItems[].vendorItemId/shippingCount/cancelCount/canceled,
            data.deliveryCompanyName/invoiceNumber(송장이 있을 때).
  송장 등록 POST  /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/orders/invoices
            (2026-10-15 공식 문서 "송장업로드 처리" 확인) 본문 vendorId·orderSheetInvoiceApplyDtos[]
            (shipmentBoxId·orderId·vendorItemId·deliveryCompanyCode·invoiceNumber·splitShipping=false·
            preSplitShipped=false·estimatedShippingDate=""). **발주서가 상품준비중(INSTRUCT)일 때만**
            가능하다. 응답 data.responseCode(0 성공·1 부분실패·99 실패·-1 결과없음)와 건별
            data.responseList[](shipmentBoxId·succeed·resultCode·retryRequired·resultMessage)를 둘 다
            본다. 송장이 이미 있으면 재등록 불가(NOT_ALLOW_INVOICE_NUMBER_RE_UPLOAD). 분리배송은 지원하지
            않는다(항상 전체배송). 같은 요청을 두 번 보냈을 때의 동작은 문서에 없다 → 작업 장부로 재요청 방지.

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

INVOICE_PATH = "/v2/providers/openapi/apis/api/v4/vendors/{vendor_id}/orders/invoices"
# 송장 업로드는 발주서가 "상품준비중"일 때만 받아 준다(공식 문서). 결제완료(ACCEPT)는 먼저 상품준비중 처리가 필요하다.
INVOICE_UPLOAD_ALLOWED_RAW_STATUS = "INSTRUCT"
# 건별 결과 코드 중 "적용 여부를 알 수 없는" 것 — 자동 재요청 금지, 발주서 조회로만 확인한다.
INVOICE_UNKNOWN_RESULT_CODES = frozenset({"UNDEFINED_ERROR_OCCUR"})

# 상품 수정(승인불필요) — 2026-10-15 공식 문서 확인: PUT .../seller-products/{sellerProductId}/partial, Body의 최상위
# outboundShippingTimeDay(기준출고일, number, "당일출고는 1")만 바꾼다(필수 항목은 sellerProductId뿐). 임시저장중·승인대기중 상품은
# 수정할 수 없다. 응답 모양은 실제로 받아 본 적이 없어 성공 판정은 최상위 code=SUCCESS와 사후 조회(GET)로 이중 확인한다.
PRODUCT_PARTIAL_PATH = "/v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{seller_product_id}/partial"
PRODUCT_DETAIL_PATH = "/v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{seller_product_id}"
MIN_OUTBOUND_DAYS = 1
MAX_OUTBOUND_DAYS = 14  # HOMEZ 자체 상한(쿠팡 공식 상한이 아님)

# 상품 아이템별 수량 변경 — 2026-10-24 공식 문서 확인(developers.coupang.com "상품 아이템별 수량 변경"):
# PUT .../vendor-items/{vendorItemId}/quantities/{quantity}, 본문 없음, 응답 {"code":"SUCCESS","message":"재고 변경을 완료했습니다."}.
# 400 = 유효하지 않은 재고수량·삭제된 옵션·없는 옵션. 판매 승인 후 vendorItemId가 발급된 옵션만 가능.
# 수량 상한·0의 의미·즉시 반영 여부·호출 한도는 문서에 없다 → 상한은 HOMEZ 자체 값, 결과는 재고 조회(GET .../inventories의 amountInStock)로 다시 확인한다.
STOCK_QUANTITY_PATH = (
    "/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{vendor_item_id}/quantities/{quantity}"
)
MAX_STOCK_QUANTITY = 99_999  # HOMEZ 자체 상한(쿠팡 공식 상한이 아님) — 잘못 친 큰 수를 막는다
MAX_VENDOR_ITEM_ID_DIGITS = 20

# 상품 아이템별 가격 변경 — 2026-10-25 공식 문서 확인(developers.coupang.com "상품 아이템별 가격 변경"):
# PUT .../vendor-items/{vendorItemId}/prices/{price}, 본문 없음, 가격은 10원 단위. 응답 {"code":"SUCCESS","message":"가격 변경을 완료했습니다."}.
# 기본(forceSalePriceUpdate=false)은 변경 전 가격 대비 최대 50% 인하·100% 인상까지만 받는다 — HOMEZ는 **force를 보내지 않고**
# 자동가격조정(apMinSalePrice·apActive)도 보내지 않는다(그 설정은 사용자가 윙에서). 결과는 재고 조회의 salePrice로 다시 확인한다.
PRICE_PATH = "/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{vendor_item_id}/prices/{price}"
PRICE_UNIT = 10
MIN_SALE_PRICE = 10
MAX_SALE_PRICE = 10_000_000  # HOMEZ 자체 상한(쿠팡 공식 상한이 아님)
COUPANG_MAX_DECREASE = 0.5  # 공식: 변경 전 가격 대비 최대 50% 인하
COUPANG_MAX_INCREASE = 1.0  # 공식: 최대 100% 인상

# 상품 수정(승인필요) — 2026-10-25 공식 문서 확인(developers.coupang.com "상품 수정 (승인필요)"):
# PUT .../seller-products, 본문은 **상품 조회 응답 전체**에서 바꿀 값만 고쳐 그대로 보낸다(sellerProductId·옵션마다 sellerProductItemId 필수 —
# 없으면 새 옵션으로 본다). "이 API를 사용하면 승인 후에 반영됩니다." 성공 예시 {"code":"200","data":{"code":"SUCCESS","data":<sellerProductId>}}.
# 승인완료 상품을 고칠 때 requested 값의 의미와 판매 유지 여부는 공식 문서에 없다(🔴 미확인 — 연동사 안내: 상태가 승인대기로 바뀌고 판매는 유지).
PRODUCT_UPDATE_PATH = "/v2/providers/seller_api/apis/api/v1/marketplace/seller-products"
MAX_PRODUCT_NAME_CHARS = 100  # 공식: sellerProductName·displayProductName 최대 100자

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


@dataclass(frozen=True)
class OrderSheetSnapshot:
    """발주서 1건의 읽기 결과. ok=False면 아래 값은 모두 알 수 없다(실패를 값으로 해석하지 않는다)."""

    ok: bool
    status: str | None = None
    # (vendorItemId, shippingCount, cancelCount) — 이미 전량 취소된 품목은 호출자가 걸러 낸다.
    items: tuple[tuple[str, int, int], ...] = ()
    delivery_company_name: str | None = None
    invoice_number: str | None = None
    detail: str = ""
    http_status: int | None = None


@dataclass(frozen=True)
class ProductDaysSnapshot:
    """등록 상품 1건의 읽기 결과. ok=False면 아래 값은 모두 알 수 없다(실패를 값으로 해석하지 않는다)."""

    ok: bool
    status_name: str | None = None  # 쿠팡이 준 원문(영문/한글)
    days: tuple[int, ...] = ()  # items[] 마다의 기준출고일(옵션 순서)
    detail: str = ""
    http_status: int | None = None


@dataclass(frozen=True)
class StockSnapshot:
    """옵션 1개의 재고·가격·판매 상태 읽기 결과(GET .../inventories). ok=False면 아래 값은 모두 알 수 없다."""

    ok: bool
    amount_in_stock: int | None = None
    sale_price: int | None = None
    on_sale: bool | None = None
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


def _int_or_zero(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


from app.domains.purchase_task.coupang_after_sales_provider import _AfterSalesMixin  # noqa: E402
from app.domains.purchase_task.coupang_inquiry_reply_provider import _InquiryReplyMixin  # noqa: E402
from app.domains.purchase_task.coupang_return_action_provider import _ReturnActionMixin  # noqa: E402
from app.domains.purchase_task.coupang_call_center_reply_provider import _CallCenterReplyMixin  # noqa: E402
from app.domains.purchase_task.coupang_exchange_provider import _ExchangeMixin  # noqa: E402


class _InvoiceMixin:
    """송장 등록(쓰기)과 발주서 조회(읽기). `CoupangChannelControlProvider`에 합쳐 쓴다."""

    @staticmethod
    def _valid_courier_code(code: str) -> bool:
        text = str(code or "")
        return 2 <= len(text) <= 20 and all(c.isupper() or c.isdigit() or c == "_" for c in text)

    @staticmethod
    def _valid_invoice_number(number: str) -> bool:
        text = str(number or "")
        return 6 <= len(text) <= 30 and all(c.isalnum() or c == "-" for c in text)

    def validate_invoice(
        self, order_id: str, shipment_box_id: str, vendor_item_ids: list[str],
        delivery_company_code: str, invoice_number: str,
    ) -> ProviderCallResult | None:
        """요청을 만들 수 없으면 사유(외부 요청 없음), 만들 수 있으면 None. 장부 선점 전에 호출한다."""

        order = str(order_id or "").strip()
        box = str(shipment_box_id or "").strip()
        items = [str(v).strip() for v in vendor_item_ids]
        if not order.isdigit() or not box.isdigit() or not items or any(not v.isdigit() for v in items):
            return ProviderCallResult(
                CallKind.ACTION_REQUIRED, None, "INVALID_TARGET",
                "주문번호·배송번호·옵션번호가 쿠팡 요청 형식(숫자)에 맞지 않아 요청하지 않았다",
            )
        if not self._valid_courier_code(delivery_company_code):
            return ProviderCallResult(
                CallKind.ACTION_REQUIRED, None, "INVALID_COURIER",
                "택배사 코드가 쿠팡 코드 형식이 아니라 요청하지 않았다 — 추측해서 채우지 않는다",
            )
        if not self._valid_invoice_number(invoice_number):
            return ProviderCallResult(
                CallKind.ACTION_REQUIRED, None, "INVALID_INVOICE_NUMBER",
                "송장번호 형식(6~30자 영문·숫자·하이픈)이 아니라 요청하지 않았다",
            )
        return None

    def upload_invoice(
        self, *, order_id: str, shipment_box_id: str, vendor_item_ids: list[str],
        delivery_company_code: str, invoice_number: str,
    ) -> ProviderCallResult:
        """송장 등록(전체배송). 호출자가 한 배송번호(박스) 단위로 호출하며, 박스의 취소되지 않은 품목을 모두 싣는다."""

        invalid = self.validate_invoice(
            order_id, shipment_box_id, vendor_item_ids, delivery_company_code, invoice_number,
        )
        if invalid is not None:
            return invalid
        path = INVOICE_PATH.format(vendor_id=quote(self.vendor_id, safe=""))
        payload = {
            "vendorId": self.vendor_id,
            "orderSheetInvoiceApplyDtos": [
                {
                    "shipmentBoxId": int(str(shipment_box_id).strip()),
                    "orderId": int(str(order_id).strip()),
                    "vendorItemId": int(str(v).strip()),
                    "deliveryCompanyCode": delivery_company_code,
                    "invoiceNumber": invoice_number,
                    "splitShipping": False,
                    "preSplitShipped": False,
                    "estimatedShippingDate": "",
                }
                for v in vendor_item_ids
            ],
        }
        sent = self._write("POST", path, payload)
        if isinstance(sent, ProviderCallResult):
            return sent
        failure = self._classify_status(sent)
        if failure is not None:
            return failure
        body = _body(sent) or {}
        data = body.get("data")
        results = data.get("responseList") if isinstance(data, dict) else None
        if not isinstance(data, dict) or not isinstance(results, list) or not results:
            return ProviderCallResult(
                CallKind.UNKNOWN, 200, "INVALID_RESPONSE",
                "200 응답이지만 건별 결과가 없어 적용 여부를 알 수 없다",
            )
        entries = [r for r in results if isinstance(r, dict)]
        response_code = data.get("responseCode")
        all_succeeded = bool(entries) and len(entries) == len(results) and all(
            e.get("succeed") is True for e in entries
        )
        if response_code == 0 and all_succeeded:
            return ProviderCallResult(CallKind.SUCCEEDED, 200, None, _summary(body))
        failed = [e for e in entries if e.get("succeed") is not True]
        codes = sorted({str(e.get("resultCode") or "").strip() for e in failed if e.get("resultCode")})
        brief = ", ".join(codes)[:120] or f"responseCode={response_code}"
        message = str((failed[0] if failed else {}).get("resultMessage") or "")[:120]
        if response_code == 0 or not failed or any(c in INVOICE_UNKNOWN_RESULT_CODES for c in codes):
            # 전체 결과와 건별 결과가 어긋나거나(성공이라는데 실패 건이 있음) 알 수 없는 오류 — 서버가
            # 처리했을 수 있어 자동 재요청하지 않고 발주서 조회로 확인한다.
            return ProviderCallResult(
                CallKind.UNKNOWN, 200, "UNCERTAIN_RESULT",
                f"쿠팡 응답으로 적용 여부를 확정할 수 없다({brief}) {message}".strip(),
            )
        return ProviderCallResult(
            CallKind.ACTION_REQUIRED, 200, "REJECTED",
            f"쿠팡이 송장 등록을 거부했다({brief}) {message}".strip(),
        )

    def read_order_sheet(self, shipment_box_id: str) -> OrderSheetSnapshot:
        """발주서 1건(배송번호)의 상태·품목·송장. 읽기 전용 GET 1회."""

        box = str(shipment_box_id or "").strip()
        if not box.isdigit():
            return OrderSheetSnapshot(False, detail="배송번호 형식 오류")
        path = ORDERSHEET_PATH.format(vendor_id=quote(self.vendor_id, safe=""), shipment_box_id=box)
        try:
            response = self._send("GET", path)
        except requests.RequestException:
            return OrderSheetSnapshot(False, detail="조회 통신 오류")
        if response.status_code != 200:
            return OrderSheetSnapshot(
                False, detail=_summary(_body(response)), http_status=response.status_code,
            )
        body = _body(response) or {}
        data = body.get("data")
        if isinstance(data, list):
            data = data[0] if len(data) == 1 and isinstance(data[0], dict) else None
        items = data.get("orderItems") if isinstance(data, dict) else None
        status = str(data.get("status") or "").strip() if isinstance(data, dict) else ""
        if not isinstance(items, list) or not status:
            return OrderSheetSnapshot(False, detail="발주서 응답 해석 불가", http_status=200)
        parsed = tuple(
            (str(i.get("vendorItemId")), _int_or_zero(i.get("shippingCount")), _int_or_zero(i.get("cancelCount")))
            for i in items if isinstance(i, dict) and str(i.get("vendorItemId") or "").isdigit()
        )
        invoice = str(data.get("invoiceNumber") or "").strip() or None
        courier = str(data.get("deliveryCompanyName") or "").strip() or None
        return OrderSheetSnapshot(True, status, parsed, courier, invoice, "", 200)

    def read_invoice_registered(self, shipment_box_id: str, invoice_number: str) -> ProviderReadResult:
        """결과불명 대조 — 발주서의 송장번호가 보낸 번호와 같으면 value=True, 송장이 없으면 False."""

        sheet = self.read_order_sheet(shipment_box_id)
        if not sheet.ok:
            return ProviderReadResult(False, None, sheet.detail, sheet.http_status)
        if sheet.invoice_number is None:
            return ProviderReadResult(True, False, "", 200)
        return ProviderReadResult(True, sheet.invoice_number == str(invoice_number).strip(), "", 200)


class _ProductDaysMixin:
    """등록 상품의 기준 출고일 변경(쓰기)과 조회(읽기)."""

    @staticmethod
    def _valid_days(days: Any) -> bool:
        return isinstance(days, int) and not isinstance(days, bool) and MIN_OUTBOUND_DAYS <= days <= MAX_OUTBOUND_DAYS

    def validate_outbound_days(self, seller_product_id: str, days: Any) -> ProviderCallResult | None:
        if not str(seller_product_id or "").strip().isdigit():
            return ProviderCallResult(
                CallKind.ACTION_REQUIRED, None, "INVALID_TARGET",
                "쿠팡 등록상품번호(sellerProductId)가 숫자가 아니라 요청하지 않았다",
            )
        if not self._valid_days(days):
            return ProviderCallResult(
                CallKind.ACTION_REQUIRED, None, "INVALID_DAYS",
                f"기준 출고일은 {MIN_OUTBOUND_DAYS}~{MAX_OUTBOUND_DAYS}일의 정수여야 해서 요청하지 않았다",
            )
        return None

    def update_outbound_days(self, seller_product_id: str, days: int) -> ProviderCallResult:
        invalid = self.validate_outbound_days(seller_product_id, days)
        if invalid is not None:
            return invalid
        product = str(seller_product_id).strip()
        path = PRODUCT_PARTIAL_PATH.format(seller_product_id=product)
        sent = self._write("PUT", path, {"sellerProductId": int(product), "outboundShippingTimeDay": days})
        if isinstance(sent, ProviderCallResult):
            return sent
        failure = self._classify_status(sent)
        if failure is not None:
            return failure
        body = _body(sent)
        code = str((body or {}).get("code") or "").strip().upper()
        if code == "SUCCESS" or code == "200":
            return ProviderCallResult(CallKind.SUCCEEDED, 200, None, _summary(body))
        if code == "ERROR":
            return ProviderCallResult(CallKind.ACTION_REQUIRED, 200, "REJECTED", _summary(body))
        return ProviderCallResult(
            CallKind.UNKNOWN, 200, "INVALID_RESPONSE",
            "200 응답이지만 성공·실패를 판정할 수 없는 본문이라 적용 여부를 알 수 없다",
        )

    def read_product_days(self, seller_product_id: str) -> ProductDaysSnapshot:
        """상품 상세(GET 1회)에서 상태와 옵션별 기준출고일을 읽는다. 하나라도 읽지 못하면 ok=False."""

        product = str(seller_product_id or "").strip()
        if not product.isdigit():
            return ProductDaysSnapshot(False, detail="등록상품번호 형식 오류")
        path = PRODUCT_DETAIL_PATH.format(seller_product_id=product)
        try:
            response = self._send("GET", path)
        except requests.RequestException:
            return ProductDaysSnapshot(False, detail="조회 통신 오류")
        if response.status_code != 200:
            return ProductDaysSnapshot(False, detail=_summary(_body(response)), http_status=response.status_code)
        body = _body(response) or {}
        data = body.get("data")
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items:
            return ProductDaysSnapshot(False, detail="상품 응답 해석 불가", http_status=200)
        days: list[int] = []
        for item in items:
            raw = item.get("outboundShippingTimeDay") if isinstance(item, dict) else None
            text = str(raw).strip() if raw is not None and not isinstance(raw, bool) else ""
            if not text.isdigit():
                return ProductDaysSnapshot(False, detail="옵션의 기준출고일을 읽지 못함", http_status=200)
            days.append(int(text))
        status = str(data.get("statusName") or "").strip() or None
        return ProductDaysSnapshot(True, status, tuple(days), "", 200)

    def read_outbound_days_applied(self, seller_product_id: str, days: int) -> ProviderReadResult:
        """결과불명 대조 — 모든 옵션의 기준출고일이 요청한 값이면 value=True."""

        snapshot = self.read_product_days(seller_product_id)
        if not snapshot.ok:
            return ProviderReadResult(False, None, snapshot.detail, snapshot.http_status)
        return ProviderReadResult(True, all(d == days for d in snapshot.days), "", 200)


class _StockMixin:
    """옵션별 재고 수량 변경(쓰기)과 재고 조회(읽기). 2026-10-24 쿠팡 쓰기 1번(사용자 지시)."""

    @staticmethod
    def _valid_vendor_item(vendor_item_id: Any) -> bool:
        text = str(vendor_item_id or "").strip()
        return text.isdigit() and len(text) <= MAX_VENDOR_ITEM_ID_DIGITS and not isinstance(vendor_item_id, bool)

    @staticmethod
    def _valid_quantity(quantity: Any) -> bool:
        return isinstance(quantity, int) and not isinstance(quantity, bool) and 0 <= quantity <= MAX_STOCK_QUANTITY

    def validate_stock(self, vendor_item_id: str, quantity: Any) -> ProviderCallResult | None:
        if not self._valid_vendor_item(vendor_item_id):
            return ProviderCallResult(
                CallKind.ACTION_REQUIRED, None, "INVALID_TARGET",
                "쿠팡 옵션번호(vendorItemId)가 숫자가 아니라 요청하지 않았다",
            )
        if not self._valid_quantity(quantity):
            return ProviderCallResult(
                CallKind.ACTION_REQUIRED, None, "INVALID_QUANTITY",
                f"재고 수량은 0~{MAX_STOCK_QUANTITY:,}의 정수여야 해서 요청하지 않았다",
            )
        return None

    def update_stock(self, vendor_item_id: str, quantity: int) -> ProviderCallResult:
        invalid = self.validate_stock(vendor_item_id, quantity)
        if invalid is not None:
            return invalid
        path = STOCK_QUANTITY_PATH.format(vendor_item_id=str(vendor_item_id).strip(), quantity=int(quantity))
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
            return ProviderCallResult(CallKind.ACTION_REQUIRED, 200, "REJECTED", _summary(body))
        return ProviderCallResult(
            CallKind.UNKNOWN, 200, "INVALID_RESPONSE",
            "200 응답이지만 성공·실패를 판정할 수 없는 본문이라 적용 여부를 알 수 없다",
        )

    def read_stock(self, vendor_item_id: str) -> StockSnapshot:
        """옵션 1개의 재고·가격·판매 상태(GET 1회). 재고 수량을 읽지 못하면 ok=False(추측하지 않는다)."""

        if not self._valid_vendor_item(vendor_item_id):
            return StockSnapshot(False, detail="옵션번호 형식 오류")
        path = INVENTORY_PATH.format(vendor_item_id=str(vendor_item_id).strip())
        try:
            response = self._send("GET", path)
        except requests.RequestException:
            return StockSnapshot(False, detail="조회 통신 오류")
        if response.status_code != 200:
            return StockSnapshot(False, detail=_summary(_body(response)), http_status=response.status_code)
        body = _body(response) or {}
        data = body.get("data")
        if str(body.get("code") or "").upper() != "SUCCESS" or not isinstance(data, dict):
            return StockSnapshot(False, detail="재고 응답 해석 불가", http_status=200)
        amount = data.get("amountInStock")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            return StockSnapshot(False, detail="재고 수량을 읽지 못함", http_status=200)
        price = data.get("salePrice")
        on_sale = data.get("onSale")
        return StockSnapshot(
            True, amount, price if isinstance(price, int) and not isinstance(price, bool) else None,
            on_sale if isinstance(on_sale, bool) else None, "", 200,
        )

    def read_stock_applied(self, vendor_item_id: str, quantity: int) -> ProviderReadResult:
        """결과불명 대조 — 재고가 요청한 수량이면 value=True. 그사이 주문으로 줄었을 수 있어 False는 '같지 않다'일 뿐이다."""

        snapshot = self.read_stock(vendor_item_id)
        if not snapshot.ok:
            return ProviderReadResult(False, None, snapshot.detail, snapshot.http_status)
        return ProviderReadResult(True, snapshot.amount_in_stock == quantity, "", 200)


class _PriceMixin:
    """옵션별 판매가 변경(쓰기)과 결과 대조(읽기 — 재고 조회의 salePrice). 2026-10-25 쿠팡 쓰기 2번."""

    @staticmethod
    def _valid_price(price: Any) -> bool:
        return (isinstance(price, int) and not isinstance(price, bool) and MIN_SALE_PRICE <= price <= MAX_SALE_PRICE
                and price % PRICE_UNIT == 0)

    def validate_price(self, vendor_item_id: str, price: Any) -> ProviderCallResult | None:
        if not _StockMixin._valid_vendor_item(vendor_item_id):
            return ProviderCallResult(
                CallKind.ACTION_REQUIRED, None, "INVALID_TARGET",
                "쿠팡 옵션번호(vendorItemId)가 숫자가 아니라 요청하지 않았다",
            )
        if not self._valid_price(price):
            return ProviderCallResult(
                CallKind.ACTION_REQUIRED, None, "INVALID_PRICE",
                f"판매가는 {MIN_SALE_PRICE}~{MAX_SALE_PRICE:,}원의 10원 단위 정수여야 해서 요청하지 않았다",
            )
        return None

    def update_price(self, vendor_item_id: str, price: int) -> ProviderCallResult:
        invalid = self.validate_price(vendor_item_id, price)
        if invalid is not None:
            return invalid
        # forceSalePriceUpdate·자동가격조정 값은 보내지 않는다 — 쿠팡의 변경 폭 제한을 안전장치로 그대로 둔다.
        path = PRICE_PATH.format(vendor_item_id=str(vendor_item_id).strip(), price=int(price))
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
            return ProviderCallResult(CallKind.ACTION_REQUIRED, 200, "REJECTED", _summary(body))
        return ProviderCallResult(
            CallKind.UNKNOWN, 200, "INVALID_RESPONSE",
            "200 응답이지만 성공·실패를 판정할 수 없는 본문이라 적용 여부를 알 수 없다",
        )

    def read_price_applied(self, vendor_item_id: str, price: int) -> ProviderReadResult:
        """결과불명 대조 — 재고 조회의 판매가가 요청한 값이면 value=True. 판매가를 읽지 못하면 ok=False."""

        snapshot = self.read_stock(vendor_item_id)
        if not snapshot.ok or snapshot.sale_price is None:
            return ProviderReadResult(False, None, snapshot.detail or "판매가를 읽지 못함", snapshot.http_status)
        return ProviderReadResult(True, snapshot.sale_price == price, "", 200)


@dataclass(frozen=True)
class ProductFullSnapshot:
    """상품 조회(GET 1회) 응답의 data 전체. ok=False면 data는 None이다(일부만 읽은 것을 값으로 쓰지 않는다)."""

    ok: bool
    data: dict | None = None
    status_name: str | None = None
    display_name: str | None = None
    seller_name: str | None = None
    detail: str = ""
    http_status: int | None = None


class _ProductNameMixin:
    """등록 상품의 상품명 수정(쓰기 — 상품 수정 승인필요 API)과 상품 전체 조회(읽기). 2026-10-25 쿠팡 쓰기 3번."""

    def read_product_full(self, seller_product_id: str) -> ProductFullSnapshot:
        product = str(seller_product_id or "").strip()
        if not product.isdigit() or len(product) > MAX_VENDOR_ITEM_ID_DIGITS:
            return ProductFullSnapshot(False, detail="등록상품번호 형식 오류")
        try:
            response = self._send("GET", PRODUCT_DETAIL_PATH.format(seller_product_id=product))
        except requests.RequestException:
            return ProductFullSnapshot(False, detail="조회 통신 오류")
        if response.status_code != 200:
            return ProductFullSnapshot(False, detail=_summary(_body(response)), http_status=response.status_code)
        body = _body(response) or {}
        data = body.get("data")
        # 실제 상품 조회에서 확인된 것은 data 구조뿐이다(최상위 code 값은 관측 기록 없음) — 명시적 ERROR만 실패로 본다.
        if str(body.get("code") or "").upper() == "ERROR" or not isinstance(data, dict):
            return ProductFullSnapshot(False, detail="상품 응답 해석 불가", http_status=200)
        if str(data.get("sellerProductId") or "").strip() != product:
            return ProductFullSnapshot(False, detail="응답의 등록상품번호가 요청과 다르다", http_status=200)
        items = data.get("items")
        if not isinstance(items, list) or not items or not all(isinstance(i, dict) for i in items):
            return ProductFullSnapshot(False, detail="상품 옵션을 읽지 못함", http_status=200)

        def text(key):
            value = data.get(key)
            return value.strip() if isinstance(value, str) and value.strip() else None

        return ProductFullSnapshot(True, data, text("statusName"), text("displayProductName"), text("sellerProductName"), "", 200)

    @staticmethod
    def _valid_product_body(seller_product_id: str, body: Any) -> bool:
        if not isinstance(body, dict) or str(body.get("sellerProductId") or "").strip() != str(seller_product_id or "").strip():
            return False
        items = body.get("items")
        # 옵션마다 sellerProductItemId가 없으면 쿠팡은 새 옵션으로 본다 — 기존 옵션을 고치는 요청에서는 절대 보내지 않는다.
        return isinstance(items, list) and bool(items) and all(
            isinstance(i, dict) and str(i.get("sellerProductItemId") or "").strip().isdigit() for i in items
        )

    def validate_product_update(self, seller_product_id: str, body: Any) -> ProviderCallResult | None:
        if not str(seller_product_id or "").strip().isdigit():
            return ProviderCallResult(
                CallKind.ACTION_REQUIRED, None, "INVALID_TARGET",
                "쿠팡 등록상품번호(sellerProductId)가 숫자가 아니라 요청하지 않았다",
            )
        if not self._valid_product_body(seller_product_id, body):
            return ProviderCallResult(
                CallKind.ACTION_REQUIRED, None, "INVALID_BODY",
                "상품 수정 본문에 등록상품번호나 옵션 번호(sellerProductItemId)가 없어 요청하지 않았다",
            )
        return None

    def update_product(self, seller_product_id: str, body: dict) -> ProviderCallResult:
        invalid = self.validate_product_update(seller_product_id, body)
        if invalid is not None:
            return invalid
        sent = self._write("PUT", PRODUCT_UPDATE_PATH, body)
        if isinstance(sent, ProviderCallResult):
            return sent
        failure = self._classify_status(sent)
        if failure is not None:
            return failure
        payload = _body(sent) or {}
        top = str(payload.get("code") or "").strip().upper()
        inner = payload.get("data")
        inner_code = str(inner.get("code") or "").strip().upper() if isinstance(inner, dict) else ""
        if top in ("200", "SUCCESS") and inner_code == "SUCCESS":
            return ProviderCallResult(CallKind.SUCCEEDED, 200, None, _summary(payload))
        if top == "ERROR" or inner_code == "ERROR":
            return ProviderCallResult(CallKind.ACTION_REQUIRED, 200, "REJECTED", _summary(inner if inner_code == "ERROR" else payload))
        return ProviderCallResult(
            CallKind.UNKNOWN, 200, "INVALID_RESPONSE",
            "200 응답이지만 성공·실패를 판정할 수 없는 본문이라 적용 여부를 알 수 없다",
        )

    def read_product_name_applied(self, seller_product_id: str, display_name: str) -> ProviderReadResult:
        """결과불명 대조 — 노출상품명이 요청한 값이면 value=True. 다르면 **판단하지 않는다**(ok=False):
        수정은 쿠팡 승인 뒤에 반영돼 '아직 안 바뀜'이 '안 받음'을 뜻하지 않는다. 그때는 사용자가 윙에서 확인한다."""

        snapshot = self.read_product_full(seller_product_id)
        if not snapshot.ok:
            return ProviderReadResult(False, None, snapshot.detail, snapshot.http_status)
        if snapshot.display_name == display_name:
            return ProviderReadResult(True, True, "", 200)
        return ProviderReadResult(False, None, "노출상품명이 아직 그대로다(쿠팡 승인 대기일 수 있음) — 윙에서 확인 필요", 200)


class CoupangChannelControlProvider(
    _InvoiceMixin, _ProductDaysMixin, _AfterSalesMixin, _StockMixin, _PriceMixin, _ProductNameMixin, _InquiryReplyMixin,
    _ReturnActionMixin, _CallCenterReplyMixin, _ExchangeMixin,
):
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
    "RequestsControlTransport", "CoupangChannelControlProvider", "OrderSheetSnapshot", "ProductDaysSnapshot",
    "CANCEL_AUTO_ALLOWED_RAW_STATUSES", "INVOICE_UPLOAD_ALLOWED_RAW_STATUS",
]
