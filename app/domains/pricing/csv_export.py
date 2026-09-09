"""
=========================================================
Homez OS

File : app/domains/pricing/csv_export.py

Gate 5(2026-08-15) — 회사별 정산/마진 CSV 내보내기(요구사항 6).
`app/domains/marketplace_listing/listing_wizard_csv_export.py`(Gate U-3)
가 이미 확립한 CSV 보안 계약(Formula Injection 방어 + UTF-8 BOM +
최대 행수 + ko-KR/en-US 헤더, `status_sync_service.py`가 원조)을
그대로 재사용한다 — 같은 정책을 두 곳에서 다르게 만들지 않기 위해
상수/헬퍼를 동일하게 복제한다(도메인 슬라이스 간 교차 import를 하지
않는 이 저장소의 기존 관례).

`include_economics=False`면 금액/비율 관련 컬럼은 값을 가리는 게
아니라 헤더/셀 자체를 CSV에서 제외한다(Gate U-1과 동일 원칙,
listing_wizard_csv_export.py와 동일 구현 형태).

XLSX는 이번 범위에서 구현하지 않는다 — 이 저장소에 스프레드시트
라이브러리 의존성이 아직 없고(신규 의존성 추가는 별도 승인 대상,
CLAUDE.md Whitelist), 기존 `listing_wizard.export`(Gate U-3) 선례도
CSV까지만 구현했다. 회계 소프트웨어 연동 경계는
`docs/adr/0003-accounting-software-integration-boundary.md` 참고.
=========================================================
"""

from __future__ import annotations

import csv
import io

from app.domains.pricing.model import MarginSnapshot
from app.domains.pricing.model import PriceChangeRequest
from app.domains.pricing.model import ProductPricing
from app.domains.pricing.model import SettlementReconciliation

_FORMULA_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value) -> str:

    text = str(value) if value is not None else ""

    if text and text[0] in _FORMULA_INJECTION_PREFIXES:
        return "'" + text

    return text


CSV_UTF8_BOM = "﻿"

MAX_CSV_EXPORT_ROWS = 5000

_BASE_COLUMN_KEYS = [
    "listing_id", "pending_price_change_status", "version", "updated_at",
]
_ECONOMICS_COLUMN_KEYS = [
    "current_sale_price", "cost_of_goods", "expected_margin_amount",
    "expected_margin_rate", "latest_actual_margin_amount",
    "latest_actual_margin_rate", "reconciliation_status",
    "reconciliation_variance_amount",
]

_HEADER_LABELS = {
    "ko-KR": {
        "listing_id": "리스팅 ID",
        "pending_price_change_status": "진행 중 가격변경 요청",
        "version": "버전",
        "updated_at": "수정 시각",
        "current_sale_price": "현재 판매가",
        "cost_of_goods": "원가",
        "expected_margin_amount": "예상 마진액",
        "expected_margin_rate": "예상 마진율",
        "latest_actual_margin_amount": "최근 실제 마진액",
        "latest_actual_margin_rate": "최근 실제 마진율",
        "reconciliation_status": "정산 대사 상태",
        "reconciliation_variance_amount": "정산 괴리액",
    },
    "en-US": {
        "listing_id": "Listing ID",
        "pending_price_change_status": "Pending Price Change",
        "version": "Version",
        "updated_at": "Updated At",
        "current_sale_price": "Current Sale Price",
        "cost_of_goods": "Cost of Goods",
        "expected_margin_amount": "Expected Margin Amount",
        "expected_margin_rate": "Expected Margin Rate",
        "latest_actual_margin_amount": "Latest Actual Margin Amount",
        "latest_actual_margin_rate": "Latest Actual Margin Rate",
        "reconciliation_status": "Reconciliation Status",
        "reconciliation_variance_amount": "Reconciliation Variance",
    },
}
DEFAULT_CSV_LOCALE = "ko-KR"


def build_accounting_csv(
    rows: list[dict], *,
    include_economics: bool, locale: str = DEFAULT_CSV_LOCALE,
) -> str:
    """
    순수 문자열 빌더 — DB 접근·행수 상한 확인은 호출자(서비스 계층)의
    책임이다. `rows`는 서비스가 이미 조립한 dict 목록이다(이 함수는
    ORM에 접근하지 않는다 — listing_wizard_csv_export.py의 build_
    wizards_csv와 동일한 계층 분리).
    """

    header_labels = _HEADER_LABELS.get(
        locale, _HEADER_LABELS[DEFAULT_CSV_LOCALE],
    )
    column_keys = list(_BASE_COLUMN_KEYS)
    if include_economics:
        column_keys += _ECONOMICS_COLUMN_KEYS

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([header_labels[key] for key in column_keys])

    for row in rows:
        writer.writerow([_csv_safe(row.get(key, "")) for key in column_keys])

    return CSV_UTF8_BOM + buffer.getvalue()


def build_accounting_row(
    pricing: ProductPricing,
    pending_request: PriceChangeRequest | None,
    latest_actual: MarginSnapshot | None,
    reconciliation: SettlementReconciliation | None,
) -> dict:
    """ProductPricing 1건 + 연관 조회 결과 → CSV 행 dict."""

    return {
        "listing_id": pricing.listing_id,
        "pending_price_change_status": (
            pending_request.status if pending_request is not None else ""
        ),
        "version": pricing.version,
        "updated_at": (
            pricing.updated_at.isoformat() if pricing.updated_at else ""
        ),
        "current_sale_price": pricing.current_sale_price,
        "cost_of_goods": pricing.cost_of_goods,
        "expected_margin_amount": pricing.expected_margin_amount,
        "expected_margin_rate": pricing.expected_margin_rate,
        "latest_actual_margin_amount": (
            latest_actual.margin_amount if latest_actual is not None else ""
        ),
        "latest_actual_margin_rate": (
            latest_actual.margin_rate if latest_actual is not None else ""
        ),
        "reconciliation_status": (
            reconciliation.status if reconciliation is not None else ""
        ),
        "reconciliation_variance_amount": (
            reconciliation.variance_amount
            if reconciliation is not None
            and reconciliation.variance_amount is not None
            else ""
        ),
    }


__all__ = [
    "build_accounting_csv",
    "build_accounting_row",
    "CSV_UTF8_BOM",
    "MAX_CSV_EXPORT_ROWS",
    "DEFAULT_CSV_LOCALE",
]
