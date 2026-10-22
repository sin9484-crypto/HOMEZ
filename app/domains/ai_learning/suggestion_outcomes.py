"""
=========================================================
Homez OS

File : app/domains/ai_learning/suggestion_outcomes.py

2026-10-23 — 마케팅 AI M4-full(제안과 성과 연결): **채택한 제안**(상품명·검색어·가격)을 쿠팡 옵션(`vendor_item_id`)에 연결해, 제안한 날 **전·후**의 판매·반품을 옵션별로 나란히 보여 주는
순수 함수. DB·네트워크·AI 없음, 저장 없음 — 제안 기록을 담을 표는 아직 만들지 않았다(`docs/migration_drafts/20261023_00_create_marketing_suggestion_record_schema.sql.draft` 초안만 있고 적용하지 않았다).
기록을 어디에 저장할지는 사용자 승인 뒤의 일이라, 이 모듈은 호출하는 쪽이 넘겨 준 기록 목록(`records`)과 매출내역 줄(`rows`, `read_revenue_history`가 읽은 줄)만 본다.

기준:
  - **"맞았다/틀렸다"를 판정하지 않는다.** 전·후 숫자를 보여 줄 뿐이며(`causal: False`) 날씨·계절·할인·광고·경쟁 변화를 가를 수 없어 제안 때문에 달라졌다고 말하지 않는다.
  - **표본이 작으면 판단하지 않는다.** 전·후 어느 쪽이든 판매 주문 수가 `marketing_outcomes.REFERENCE_ORDERS`(10건) 미만이면 `INSUFFICIENT`(판단 불가) — 숫자는 참고로 내되 비교 문구를 만들지 않는다.
  - 채택하지 않은 제안(`adopted`가 참이 아님)·옵션 ID가 없는 기록·제안일이 읽히지 않는 기록·전 또는 후 기간이 없는 기록은 각각 이유를 달아 **평가하지 않는다**(모르면 평가하지 않는다).
  - 전·후 기간은 읽은 매출내역의 기간(처음~마지막 인식일)을 제안일에서 나눈 것이다. 제안일 당일은 '후'에 넣는다. 기간 길이가 달라 **하루 평균 판매 수량**도 같이 낸다.
  - 같은 옵션에 제안이 여러 번 있으면 기록마다 따로 평가하되 서로 겹치는 기간이 있다는 경고(`OVERLAPPING_SUGGESTIONS`)를 붙인다.
  - 집계는 `marketing_outcomes.summarize`를 그대로 쓴다(중복 줄 제거·부호 처리 동일).
=========================================================
"""

from __future__ import annotations

import re
from datetime import date
from datetime import datetime
from decimal import ROUND_HALF_UP
from decimal import Decimal
from typing import Any

from app.domains.ai_learning import marketing_outcomes as mo

KINDS = ("PRODUCT_NAME", "SEARCH_KEYWORD", "PRICE")
MAX_RECORDS = 200
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_PER_DAY = Decimal("0.01")

NOTES = (
    "제안 전·후 숫자를 나란히 보여 줄 뿐 제안이 맞았는지는 판정하지 않아요(계절·할인·광고·경쟁 변화를 가를 수 없어요).",
    "표본이 작으면 판단하지 않아요.",
)


def _day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and _DATE.match(value):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _row_day(row: dict) -> date | None:
    return _day(row.get("recognition_date"))


def _side(rows: list[dict], vendor_item_id: str, days: int) -> dict[str, Any]:
    summary = mo.summarize([r for r in rows if str(r.get("vendor_item_id")).strip() == vendor_item_id])
    item = summary["items"][0] if summary["items"] else None
    orders = item["orders"] if item else 0
    units = item["sale_units"] if item else 0
    return {
        "days": days, "orders": orders, "sample": mo.sample_level(orders), "sale_units": units, "return_rate": item["return_rate"] if item else None,
        "units_per_day": str((Decimal(units) / Decimal(days)).quantize(_PER_DAY, rounding=ROUND_HALF_UP)) if days > 0 else None,
        "refund_units": item["refund_units"] if item else 0,
    }


def evaluate(records: list[dict], rows: list[dict]) -> dict[str, Any]:
    """records: [{"id", "kind", "vendor_item_id", "suggested_at", "adopted", "suggested_value"(선택)}]. rows: 쿠팡 매출내역 줄. 반환: 기록마다 상태와 전·후 숫자."""

    all_records = list(records or [])
    records = all_records[:MAX_RECORDS]
    days = [d for d in (_row_day(r) for r in rows or []) if d is not None]
    first, last = (min(days), max(days)) if days else (None, None)
    out: list[dict[str, Any]] = []
    for record in records:
        base = {"id": record.get("id"), "kind": record.get("kind"), "vendor_item_id": record.get("vendor_item_id"), "suggested_value": str(record.get("suggested_value") or "")[:120] or None}
        if record.get("kind") not in KINDS:
            out.append({**base, "status": "NOT_EVALUATED", "reason": "UNKNOWN_KIND"})
            continue
        if record.get("adopted") is not True:
            out.append({**base, "status": "NOT_EVALUATED", "reason": "NOT_ADOPTED"})
            continue
        vendor = str(record.get("vendor_item_id") or "").strip()
        if not vendor:
            out.append({**base, "status": "NOT_EVALUATED", "reason": "NOT_LINKED"})
            continue
        suggested = _day(record.get("suggested_at"))
        if suggested is None:
            out.append({**base, "status": "NOT_EVALUATED", "reason": "SUGGESTED_AT_UNREADABLE"})
            continue
        if first is None or last is None:
            out.append({**base, "status": "NOT_EVALUATED", "reason": "NO_REVENUE_ROWS"})
            continue
        days_before, days_after = (suggested - first).days, (last - suggested).days + 1
        if days_before <= 0 or days_after <= 0:
            out.append({**base, "status": "NOT_EVALUATED", "reason": "NO_BEFORE_OR_AFTER_PERIOD", "window": {"from": first.isoformat(), "to": last.isoformat()}})
            continue
        before_rows = [r for r in rows if (_row_day(r) is not None and _row_day(r) < suggested)]
        after_rows = [r for r in rows if (_row_day(r) is not None and _row_day(r) >= suggested)]
        before, after = _side(before_rows, vendor, days_before), _side(after_rows, vendor, days_after)
        enough = "INSUFFICIENT" not in (before["sample"], after["sample"])
        out.append({**base, "status": "OBSERVED" if enough else "INSUFFICIENT", "reason": None if enough else "SAMPLE_TOO_SMALL", "causal": False,
                    "suggested_at": suggested.isoformat(), "before": before, "after": after})

    by_option: dict[str, list[dict[str, Any]]] = {}
    for entry in out:
        if entry["status"] in ("OBSERVED", "INSUFFICIENT"):
            by_option.setdefault(entry["vendor_item_id"], []).append(entry)
    for entries in by_option.values():
        if len(entries) > 1:
            for entry in entries:
                entry["warnings"] = ["OVERLAPPING_SUGGESTIONS"]
    return {
        "period": {"from": first.isoformat() if first else None, "to": last.isoformat() if last else None},
        "results": out,
        "counts": {s: sum(1 for e in out if e["status"] == s) for s in ("OBSERVED", "INSUFFICIENT", "NOT_EVALUATED")},
        "omitted_records": max(0, len(all_records) - MAX_RECORDS),
        "notes": list(NOTES),
    }


__all__ = ["evaluate", "KINDS", "MAX_RECORDS"]
