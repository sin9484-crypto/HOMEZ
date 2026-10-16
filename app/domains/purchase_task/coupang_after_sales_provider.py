"""
=========================================================
Homez OS

File : app/domains/purchase_task/coupang_after_sales_provider.py

2026-10-16 — 배송 이후(반품·민원·정산)를 쿠팡에서 **읽기만** 하는 제공자 조각(`CoupangChannelControlProvider`에 섞어 쓴다).

공식 문서 확인(2026-10-16, developers.coupang.com):
  - 반품/취소 요청 목록  GET /v2/providers/openapi/apis/api/v6/vendors/{vendorId}/returnRequests
      searchType=timeFrame, createdAtFrom/To(yyyy-MM-ddTHH:mm), cancelType=RETURN|CANCEL, status(RU·UC·CC·PR, 선택),
      기간 최대 31일(길면 412 시간초과), timeFrame에서는 nextToken·maxPerPage·orderId 미지원.
  - 상품 문의  GET /v2/providers/openapi/apis/api/v5/vendors/{vendorId}/onlineInquiries
      answeredType(ALL|ANSWERED|NOANSWER), inquiryStartAt/EndAt(yyyy-MM-dd, 기간 최대 7일), pageNum, pageSize(최대 50).
  - 콜센터 문의  GET /v2/providers/openapi/apis/api/v5/vendors/{vendorId}/callCenterInquiries
      partnerCounselingStatus(NONE|ANSWER|NO_ANSWER|TRANSFER), inquiryStartAt/EndAt(최대 7일), pageNum, pageSize(최대 30).
  - 정산 내역  GET /v2/providers/marketplace_openapi/apis/api/v1/settlement-histories
      revenueRecognitionYearMonth(YYYY-MM, 필수). 문서의 응답 예시는 2019년 것이라 실제 응답 모양은 첫 호출 때 확인한다.

규칙:
  - GET만 한다. 쿠팡에 쓰지 않는다(답변·수령확인·환불 등은 이 조각에 없다).
  - **개인정보를 가져오지 않는다**: 콜센터 문의 응답에 구매자 이메일·전화번호가 있지만 파싱 단계에서 버리고, 문의 본문·반품 사유 본문·
    수취인 정보도 저장·반환하지 않는다. 남기는 것은 ID·상태·시각·건수·금액·주문번호뿐이다.
  - 응답이 예상 모양이 아니면 추측하지 않고 `ok=False`로 돌려준다.
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any
from urllib.parse import quote
from urllib.parse import urlencode

import requests


RETURN_REQUESTS_PATH = "/v2/providers/openapi/apis/api/v6/vendors/{vendor_id}/returnRequests"
ONLINE_INQUIRIES_PATH = "/v2/providers/openapi/apis/api/v5/vendors/{vendor_id}/onlineInquiries"
CALL_CENTER_INQUIRIES_PATH = "/v2/providers/openapi/apis/api/v5/vendors/{vendor_id}/callCenterInquiries"
SETTLEMENT_HISTORIES_PATH = "/v2/providers/marketplace_openapi/apis/api/v1/settlement-histories"

KST = timezone(timedelta(hours=9))
MAX_RETURN_RANGE_DAYS = 31
MAX_INQUIRY_RANGE_DAYS = 7
MAX_PAGES = 3  # 문의 목록의 한 번 읽기 상한(쪽수만큼 호출이 늘기 때문)


@dataclass(frozen=True)
class AfterSalesRead:
    """읽기 결과. ok=False면 rows는 비어 있고 알 수 없는 것으로 다룬다."""

    ok: bool
    rows: tuple[dict, ...] = ()
    detail: str = ""
    http_status: int | None = None
    calls: int = 1
    truncated: bool = False


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


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None and not isinstance(value, (dict, list)) else ""
    return text or None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


class _AfterSalesMixin:
    """`self._send(method, path, query=...)`, `self.vendor_id`, `self._now()`가 있는 제공자에 섞는다."""

    # ---- 반품·취소 요청 ----

    def read_return_requests(self, *, days: int = 7, cancel_type: str = "RETURN") -> AfterSalesRead:
        """최근 `days`일(최대 31)에 접수된 반품(또는 취소) 요청 목록. 1회 호출."""

        if cancel_type not in ("RETURN", "CANCEL") or not 1 <= int(days) <= MAX_RETURN_RANGE_DAYS:
            return AfterSalesRead(False, detail="조회 조건 오류")
        end = self._now().astimezone(KST).replace(second=0, microsecond=0)
        start = end - timedelta(days=int(days))
        query = urlencode({
            "searchType": "timeFrame", "createdAtFrom": start.strftime("%Y-%m-%dT%H:%M"),
            "createdAtTo": end.strftime("%Y-%m-%dT%H:%M"), "cancelType": cancel_type,
        }, quote_via=quote)
        response, failure = self._after_sales_get(RETURN_REQUESTS_PATH.format(vendor_id=quote(self.vendor_id, safe="")), query)
        if failure is not None:
            return failure
        data = (_body(response) or {}).get("data")
        if data is None:
            data = []
        if not isinstance(data, list):
            return AfterSalesRead(False, detail="반품 응답 해석 불가", http_status=200)
        rows = []
        for item in data:
            if not isinstance(item, dict) or _int(item.get("receiptId")) is None:
                continue
            items = item.get("returnItems") if isinstance(item.get("returnItems"), list) else []
            rows.append({
                "receipt_id": _int(item.get("receiptId")), "order_id": _text(item.get("orderId")),
                "receipt_type": _text(item.get("receiptType")), "status": _text(item.get("receiptStatus")),
                "created_at": _text(item.get("createdAt")), "modified_at": _text(item.get("modifiedAt")),
                "complete_at": _text(item.get("completeConfirmDate")), "reason_category": _text(item.get("cancelReasonCategory1")),
                "fault_by": _text(item.get("faultByType")), "release_stop": _text(item.get("releaseStopStatus")),
                "count": sum(_int(i.get("cancelCount")) or 0 for i in items if isinstance(i, dict)),
            })
        return AfterSalesRead(True, tuple(rows), "", 200)

    # ---- 문의(상품 문의·콜센터 문의) ----

    def read_online_inquiries(self, *, days: int = 7) -> AfterSalesRead:
        return self._read_inquiries(
            ONLINE_INQUIRIES_PATH, {"answeredType": "ALL"}, page_size=50, days=days, parse=self._parse_online_inquiry,
        )

    def read_call_center_inquiries(self, *, days: int = 7) -> AfterSalesRead:
        return self._read_inquiries(
            CALL_CENTER_INQUIRIES_PATH, {"partnerCounselingStatus": "NONE"}, page_size=30, days=days, parse=self._parse_call_inquiry,
        )

    def _read_inquiries(self, path_template: str, fixed: dict, *, page_size: int, days: int, parse) -> AfterSalesRead:
        if not 1 <= int(days) <= MAX_INQUIRY_RANGE_DAYS:
            return AfterSalesRead(False, detail="조회 기간 오류(최대 7일)")
        end = self._now().astimezone(KST).date()
        start = end - timedelta(days=int(days) - 1)
        path = path_template.format(vendor_id=quote(self.vendor_id, safe=""))
        rows: list[dict] = []
        calls, page, total_pages = 0, 1, 1
        while page <= total_pages and page <= MAX_PAGES:
            query = urlencode({**fixed, "inquiryStartAt": start.isoformat(), "inquiryEndAt": end.isoformat(),
                               "pageNum": page, "pageSize": page_size}, quote_via=quote)
            response, failure = self._after_sales_get(path, query)
            calls += 1
            if failure is not None:
                return AfterSalesRead(False, tuple(rows), failure.detail, failure.http_status, calls)
            data = (_body(response) or {}).get("data")
            content = data.get("content") if isinstance(data, dict) else None
            if not isinstance(content, list):
                return AfterSalesRead(False, tuple(rows), "문의 응답 해석 불가", 200, calls)
            for item in content:
                if isinstance(item, dict):
                    row = parse(item)
                    if row is not None:
                        rows.append(row)
            pagination = data.get("pagination") if isinstance(data.get("pagination"), dict) else {}
            total_pages = _int(pagination.get("totalPages")) or 1
            page += 1
        return AfterSalesRead(True, tuple(rows), "", 200, calls, truncated=total_pages > MAX_PAGES)

    @staticmethod
    def _parse_online_inquiry(item: dict) -> dict | None:
        inquiry_id = _int(item.get("inquiryId"))
        if inquiry_id is None:
            return None
        comments = item.get("commentDtoList") if isinstance(item.get("commentDtoList"), list) else []
        order_ids = [str(o) for o in item.get("orderIds", []) if isinstance(o, (int, str))] if isinstance(item.get("orderIds"), list) else []
        return {
            "inquiry_id": inquiry_id, "kind": "ONLINE", "order_ids": order_ids[:5], "inquiry_at": _text(item.get("inquiryAt")),
            "answered": bool(comments), "needs_answer": not comments, "seller_product_id": _text(item.get("sellerProductId")),
        }

    @staticmethod
    def _parse_call_inquiry(item: dict) -> dict | None:
        inquiry_id = _int(item.get("inquiryId"))
        if inquiry_id is None:
            return None
        replies = item.get("replies") if isinstance(item.get("replies"), list) else []
        needs = _text(item.get("csPartnerCounselingStatus")) == "requestAnswer" or any(
            isinstance(r, dict) and r.get("needAnswer") is True for r in replies
        )
        # 구매자 이메일·전화번호·문의 본문은 읽지 않는다(개인정보).
        return {
            "inquiry_id": inquiry_id, "kind": "CALL", "order_ids": [str(item["orderId"])] if _text(item.get("orderId")) else [],
            "inquiry_at": _text(item.get("inquiryAt")), "answered": not needs, "needs_answer": needs,
            "status": _text(item.get("inquiryStatus")), "seller_product_id": None,
        }

    # ---- 정산 ----

    def read_settlement_histories(self, year_month: str) -> AfterSalesRead:
        """매출 인식 월(YYYY-MM)의 정산 내역. 1회 호출."""

        try:
            datetime.strptime(str(year_month), "%Y-%m")
        except ValueError:
            return AfterSalesRead(False, detail="정산 월 형식 오류(YYYY-MM)")
        query = urlencode({"revenueRecognitionYearMonth": year_month}, quote_via=quote)
        response, failure = self._after_sales_get(SETTLEMENT_HISTORIES_PATH, query)
        if failure is not None:
            return failure
        # 실제 응답(2026-10-16 첫 호출)은 최상위가 배열이다(내역이 없으면 `[]`). 문서 예시의 `{"data": [...]}` 모양도 함께 받는다.
        try:
            raw = response.json()
        except ValueError:
            return AfterSalesRead(False, detail="정산 응답 해석 불가", http_status=200)
        data = raw.get("data") if isinstance(raw, dict) else raw
        if isinstance(data, dict):
            data = data.get("settlementHistories") if isinstance(data.get("settlementHistories"), list) else [data]
        if not isinstance(data, list):
            return AfterSalesRead(False, detail="정산 응답 해석 불가", http_status=200)
        rows = []
        for item in data:
            if not isinstance(item, dict):
                continue
            rows.append({
                "year_month": _text(item.get("revenueRecognitionYearMonth")) or year_month, "type": _text(item.get("settlementType")),
                "settlement_date": _text(item.get("settlementDate")), "status": _text(item.get("status")),
                "period_from": _text(item.get("revenueRecognitionDateFrom")), "period_to": _text(item.get("revenueRecognitionDateTo")),
                "total_sale": _number(item.get("totalSale")), "service_fee": _number(item.get("serviceFee")),
                "target_amount": _number(item.get("settlementTargetAmount")), "settlement_amount": _number(item.get("settlementAmount")),
                "last_amount": _number(item.get("lastAmount")), "final_amount": _number(item.get("finalAmount")),
                "deduction": _number(item.get("deductionAmount")),
            })  # 계좌 정보(예금주·은행·계좌번호)는 읽지 않는다.
        return AfterSalesRead(True, tuple(rows), "", 200)

    # ---- 공통 ----

    def _after_sales_get(self, path: str, query: str) -> tuple[requests.Response | None, AfterSalesRead | None]:
        try:
            response = self._send("GET", path, query=query)
        except requests.Timeout:
            return None, AfterSalesRead(False, detail="응답 시간 초과(기간을 줄여 다시 시도)")
        except requests.RequestException:
            return None, AfterSalesRead(False, detail="조회 통신 오류")
        if response.status_code != 200:
            return None, AfterSalesRead(False, detail=_summary(_body(response)), http_status=response.status_code)
        return response, None


__all__ = ["AfterSalesRead"]
