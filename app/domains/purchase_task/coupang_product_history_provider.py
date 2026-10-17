"""
=========================================================
Homez OS

File : app/domains/purchase_task/coupang_product_history_provider.py

2026-10-17 — 등록한 상품의 쿠팡 심사·판매 상태와 상태 변경 이력(반려 사유)을 **읽기만** 하는 제공자(연동 C단계).

공식 문서 확인(2026-10-17, developers.coupangcorp.com — 읽기 전용 확인):
  - 상품 상태변경이력 조회  GET /v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{sellerProductId}/histories
      응답 data[]: 문서는 status·comment·createdBy·createdAt이라 하나 **실제 응답의 키는 statusName·comment·createdBy·createdAt**이다(2026-10-18 읽기 확인,
      statusName 값: 임시저장·승인대기중·승인완료·승인반려). `comment`에 반려 사유가 실제로 담긴다(상세설명 이미지 다운로드 실패 문구 확인).
  - 상품 조회(상태)  GET /v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{sellerProductId} — `data.statusName`.

규칙:
  - GET만 한다. 쿠팡에 쓰지 않는다(상품 수정·재제출·판매중지는 이 파일에 없다).
  - 개인·내부 정보를 남기지 않는다: 이력의 `createdBy`는 버리고 상태·사유 문구(300자 제한)·시각만 돌려준다.
  - 응답이 예상 모양이 아니면 추측하지 않고 `ok=False`로 돌려준다. 정렬·형식이 문서와 다를 수 있어 시각으로 직접 정렬한다.
=========================================================
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.domains.marketplace_listing.coupang_live_provider import CANONICAL_STATUS_MAP
from app.domains.purchase_task.coupang_after_sales_provider import AfterSalesRead
from app.domains.purchase_task.coupang_after_sales_provider import _body
from app.domains.purchase_task.coupang_after_sales_provider import _summary
from app.domains.purchase_task.coupang_after_sales_provider import _text
from app.domains.purchase_task.coupang_channel_control_provider import CoupangChannelControlProvider

PRODUCT_PATH = "/v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{seller_product_id}"
HISTORY_PATH = PRODUCT_PATH + "/histories"
MAX_REASON_CHARS = 300


def _valid_product_id(seller_product_id: Any) -> str | None:
    text = str(seller_product_id).strip() if seller_product_id is not None else ""
    return text if text.isdigit() and 0 < len(text) <= 20 else None


class _ProductStatusMixin:
    """`self._send(method, path, query=...)`가 있는 제공자에 섞는다."""

    def read_product_status(self, seller_product_id: Any) -> AfterSalesRead:
        product_id = _valid_product_id(seller_product_id)
        if product_id is None:
            return AfterSalesRead(ok=False, detail="상품 번호가 올바르지 않다")
        try:
            response = self._send("GET", PRODUCT_PATH.format(seller_product_id=quote(product_id)))
        except Exception as exc:  # noqa: BLE001 — 네트워크 예외 종류만 남긴다
            return AfterSalesRead(ok=False, detail=f"요청 실패({type(exc).__name__})")
        body = _body(response)
        if response.status_code == 404:
            return AfterSalesRead(ok=False, detail="쿠팡에 이 상품이 없다", http_status=404)
        if response.status_code != 200 or body is None:
            return AfterSalesRead(ok=False, detail=_summary(body) or f"HTTP {response.status_code}", http_status=response.status_code)
        data = body.get("data")
        if not isinstance(data, dict):
            return AfterSalesRead(ok=False, detail="응답에 상태 정보(data)가 없다", http_status=response.status_code)
        raw = _text(data.get("statusName"))
        if raw is None:
            return AfterSalesRead(ok=False, detail="응답에 statusName이 없다", http_status=response.status_code)
        row = {
            "raw": raw,
            "status": CANONICAL_STATUS_MAP.get(raw, "UNKNOWN"),  # 관측된 적 없는 표기는 UNKNOWN으로 두고 원문(raw)을 보존한다
            "name": (_text(data.get("sellerProductName")) or "")[:80],
        }
        return AfterSalesRead(ok=True, rows=(row,), http_status=response.status_code)

    def read_product_history(self, seller_product_id: Any) -> AfterSalesRead:
        product_id = _valid_product_id(seller_product_id)
        if product_id is None:
            return AfterSalesRead(ok=False, detail="상품 번호가 올바르지 않다")
        try:
            response = self._send("GET", HISTORY_PATH.format(seller_product_id=quote(product_id)))
        except Exception as exc:  # noqa: BLE001
            return AfterSalesRead(ok=False, detail=f"요청 실패({type(exc).__name__})")
        body = _body(response)
        if response.status_code != 200 or body is None:
            return AfterSalesRead(ok=False, detail=_summary(body) or f"HTTP {response.status_code}", http_status=response.status_code)
        data = body.get("data")
        if not isinstance(data, list):
            return AfterSalesRead(ok=False, detail="응답에 이력 목록(data)이 없다", http_status=response.status_code)
        rows = []
        for item in data:
            if not isinstance(item, dict):
                continue
            # 실제 응답(2026-10-18 읽기 확인)의 상태 키는 `statusName`이고(값: 임시저장·승인대기중·승인완료·승인반려), 문서의 `status`는 비어 있었다.
            # 둘 다 받는다(실제 키 우선). 응답 최상위에는 `nextToken`이 있어 다음 쪽이 있을 수 있으나 최신 이력이 앞이라 첫 쪽만 읽는다.
            rows.append({
                "status": _text(item.get("statusName")) or _text(item.get("status")) or "",
                "comment": (_text(item.get("comment")) or "")[:MAX_REASON_CHARS],
                "at": _text(item.get("createdAt")) or "",
            })
        rows.sort(key=lambda r: r["at"], reverse=True)  # 문서에 정렬 기준이 없어 시각으로 직접 정렬한다(최신이 앞)
        return AfterSalesRead(ok=True, rows=tuple(rows), http_status=response.status_code)


class CoupangProductStatusProvider(_ProductStatusMixin, CoupangChannelControlProvider):
    """상품 상태·이력 읽기를 더한 제공자 — 기존 제공자의 서명·전송·자격증명 처리를 그대로 쓴다."""
