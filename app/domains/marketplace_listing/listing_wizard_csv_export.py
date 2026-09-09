"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/listing_wizard_csv_export.py

Gate U-3(2026-08-10) — 상품등록 마법사 CSV 내보내기. Gate H가 이미
정립한 status_sync_service.py의 CSV 보안 계약(Formula Injection 방어
+ UTF-8 BOM + 최대 행수 + ko-KR/en-US 헤더)을 그대로 재사용한다(같은
정책을 두 곳에서 서로 다르게 만들지 않기 위해 상수/헬퍼를 동일하게
복제한다 — 도메인 슬라이스 간 교차 import는 하지 않는 기존 관례를
따른다).

Gate U-1과 동일한 원칙 — `include_economics=False`면 금액 관련 컬럼은
값을 가리는 게 아니라 헤더/셀 자체를 CSV에서 제외한다(컬럼 수 자체가
달라진다).
=========================================================
"""

from __future__ import annotations

import csv
import io
import json

from app.domains.marketplace_listing.model import ListingWizard

# --------------------------------------------------
# CSV Formula Injection 방어 (status_sync_service.py와 동일한 정책)
# --------------------------------------------------

_FORMULA_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value) -> str:

    text = str(value)

    if text and text[0] in _FORMULA_INJECTION_PREFIXES:
        return "'" + text

    return text


# Excel(Windows)이 확장자만 보고 시스템 로캘로 잘못 해석해 한글이
# 깨지는 것을 막기 위한 UTF-8 BOM.
CSV_UTF8_BOM = "﻿"

# CSV 내보내기가 무제한 행을 만들지 않도록 상한을 둔다. 초과 시 조용히
# 잘라내지 않고 명시적으로 차단한다(호출자가 BadRequestException을
# 던진다 — 이 모듈은 순수 문자열 빌더라 예외를 만들지 않는다).
MAX_CSV_EXPORT_ROWS = 5000

_BASE_COLUMN_KEYS = [
    "wizard_id", "status", "current_step", "source_type", "product_name",
    "channel_count", "version", "created_at", "updated_at",
]
_ECONOMICS_COLUMN_KEYS = [
    "cost_of_goods_total", "sale_price_total", "margin_amount_total",
    "margin_rate_avg",
]

_HEADER_LABELS = {
    "ko-KR": {
        "wizard_id": "위저드 ID",
        "status": "상태",
        "current_step": "현재 단계",
        "source_type": "출처 유형",
        "product_name": "상품명",
        "channel_count": "선택 채널 수",
        "version": "버전",
        "created_at": "생성 시각",
        "updated_at": "수정 시각",
        "cost_of_goods_total": "원가 합계",
        "sale_price_total": "판매가 합계",
        "margin_amount_total": "마진 합계",
        "margin_rate_avg": "평균 마진율",
    },
    "en-US": {
        "wizard_id": "Wizard ID",
        "status": "Status",
        "current_step": "Current Step",
        "source_type": "Source Type",
        "product_name": "Product Name",
        "channel_count": "Channel Count",
        "version": "Version",
        "created_at": "Created At",
        "updated_at": "Updated At",
        "cost_of_goods_total": "Total Cost of Goods",
        "sale_price_total": "Total Sale Price",
        "margin_amount_total": "Total Margin",
        "margin_rate_avg": "Average Margin Rate",
    },
}
DEFAULT_CSV_LOCALE = "ko-KR"


def _economics_totals(wizard: ListingWizard) -> dict[str, str]:

    results = json.loads(wizard.economics_result_json or "[]")
    if not results:
        return {
            "cost_of_goods_total": "", "sale_price_total": "",
            "margin_amount_total": "", "margin_rate_avg": "",
        }

    inputs = json.loads(wizard.economics_input_json or "[]")
    cost_total = sum(
        float(item.get("cost_of_goods", 0)) for item in inputs
    )
    sale_total = sum(
        float(item.get("sale_price", 0)) for item in inputs
    )
    margin_total = sum(
        float(item.get("margin_amount", 0)) for item in results
    )
    rates = [float(item.get("margin_rate", 0)) for item in results]
    margin_rate_avg = sum(rates) / len(rates) if rates else 0.0

    return {
        "cost_of_goods_total": f"{cost_total:.2f}",
        "sale_price_total": f"{sale_total:.2f}",
        "margin_amount_total": f"{margin_total:.2f}",
        "margin_rate_avg": f"{margin_rate_avg:.4f}",
    }


def build_wizards_csv(
    wizards: list[ListingWizard], *,
    include_economics: bool, locale: str = DEFAULT_CSV_LOCALE,
) -> str:
    """
    순수 문자열 빌더 — DB 접근·행수 상한 확인은 호출자(서비스 계층)의
    책임이다. `include_economics=False`면 금액 관련 4개 컬럼은 헤더째
    빠진다(값을 빈 문자열로 가리는 게 아니다).
    """

    header_labels = _HEADER_LABELS.get(locale, _HEADER_LABELS[DEFAULT_CSV_LOCALE])
    column_keys = list(_BASE_COLUMN_KEYS)
    if include_economics:
        column_keys += _ECONOMICS_COLUMN_KEYS

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([header_labels[key] for key in column_keys])

    for wizard in wizards:
        draft = json.loads(wizard.draft_json) if wizard.draft_json else {}
        channel_selections = json.loads(wizard.channel_selections_json or "[]")

        row = {
            "wizard_id": wizard.id,
            "status": wizard.status,
            "current_step": wizard.current_step,
            "source_type": wizard.source_type,
            "product_name": draft.get("product_name", ""),
            "channel_count": len(channel_selections),
            "version": wizard.version,
            "created_at": (
                wizard.created_at.isoformat() if wizard.created_at else ""
            ),
            "updated_at": (
                wizard.updated_at.isoformat() if wizard.updated_at else ""
            ),
        }
        if include_economics:
            row.update(_economics_totals(wizard))

        writer.writerow([_csv_safe(row[key]) for key in column_keys])

    return CSV_UTF8_BOM + buffer.getvalue()


__all__ = [
    "build_wizards_csv",
    "CSV_UTF8_BOM",
    "MAX_CSV_EXPORT_ROWS",
    "DEFAULT_CSV_LOCALE",
]
