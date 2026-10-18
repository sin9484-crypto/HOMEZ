"""
=========================================================
Homez OS

File : app/domains/ai_learning/marketing_outcomes.py

2026-10-18 — 마케팅 AI M4(성과 기록·학습 보고, lite): 쿠팡 매출내역(`read_revenue_history`가 읽은 줄)을 **옵션별로 집계**하는 순수 함수. DB·네트워크·AI 없음.

무엇을 보여 주나: 옵션(`vendorItemId`)별 판매 수량·주문 수·판매액·실현 서비스이용료율·반품 수량과 반품률, 그리고 **표본 크기 판정**.
무엇을 하지 않나:
  - 어떤 제안(상품명·가격)이 맞았는지 판정하지 않는다 — 제안이 아직 어디에도 저장되지 않아 연결할 수 없다(M4-full의 일).
  - 학습·모델 적용을 하지 않는다. 표본이 작으면 "판단 불가"라고 말한다.

규칙:
  - 같은 줄(주문·옵션·종류·인식일)이 두 번 오면 한 번만 센다(쪽을 다시 읽었을 때 이중 집계 방지). 건수는 `duplicates`로 알린다.
  - 환불 줄의 수량·금액 부호는 쿠팡 문서에 없다(예시는 양수). 그래서 **절댓값**으로 세고 판매와 환불을 따로 모은다 — 서로 빼서 "순매출"을 만들지 않는다.
  - 표본(그 옵션의 판매 주문 수): 10건 미만 `INSUFFICIENT`(판단 불가), 10~49건 `REFERENCE`(참고용), 50건 이상 `ENOUGH`(학습 검토 최소 표본: `INITIAL_MIN_SAMPLE_SIZE`).
  - 반품률이 높다는 표시(`HIGH_RETURN_RATE`)는 표본이 `REFERENCE` 이상일 때만 붙이는 **참고 기준**이다(사업 규칙이 아니다).
  - 금액은 Decimal이고 응답에는 문자열로 낸다.
=========================================================
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP
from decimal import Decimal
from typing import Any

from app.domains.ai_learning.constants import INITIAL_MIN_SAMPLE_SIZE

REFERENCE_ORDERS = 10
JUDGMENT_ORDERS = INITIAL_MIN_SAMPLE_SIZE
HIGH_RETURN_RATE = Decimal("0.10")
MAX_ITEMS_SHOWN = 50
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_RATE = Decimal("0.0001")
_WON = Decimal("1")

NOTES = (
    "쿠팡이 매출로 인정한 건만 보여요(배송 완료 7일 뒤 또는 구매 확정 기준이라 최근 판매는 아직 안 보일 수 있어요).",
    "노출·클릭·전환 데이터는 쿠팡 공식 API에 없어서 판매·반품·수수료만 보여요.",
    "이 표는 어떤 제안이 맞았는지 판정하지 않아요. 표본이 작으면 판단하지 않아요.",
)


def sample_level(orders: int) -> str:
    if orders >= JUDGMENT_ORDERS:
        return "ENOUGH"
    if orders >= REFERENCE_ORDERS:
        return "REFERENCE"
    return "INSUFFICIENT"


def _dec(value: Any) -> Decimal:
    try:
        number = abs(Decimal(str(value))) if value is not None else Decimal(0)
    except Exception:  # noqa: BLE001 — 읽을 수 없는 금액은 0으로 세지 않고 줄 전체를 건너뛴다
        raise ValueError("amount") from None
    if not number.is_finite():
        raise ValueError("amount")
    return number


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    return None if denominator <= 0 else (numerator / denominator).quantize(_RATE, rounding=ROUND_HALF_UP)


def summarize(rows: list[dict]) -> dict[str, Any]:
    seen: set[tuple] = set()
    duplicates = skipped = 0
    items: dict[str, dict[str, Any]] = {}
    all_sale_orders: set[str] = set()
    dates: list[str] = []
    for row in rows:
        try:
            if row["vendor_item_id"] is None or row["order_id"] is None or not str(row["vendor_item_id"]).strip() or not str(row["order_id"]).strip():
                raise ValueError("id")   # 옵션 ID·주문번호가 없는 줄은 어느 옵션의 것인지 알 수 없다
            vendor_item_id = str(row["vendor_item_id"]).strip()
            sale_type = row["sale_type"]
            quantity = abs(int(row["quantity"]))
            amount, fee, settlement = _dec(row.get("sale_amount")), _dec(row.get("service_fee")), _dec(row.get("settlement_amount"))
            order_id = str(row["order_id"]).strip()
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        if sale_type not in ("SALE", "REFUND"):
            skipped += 1
            continue
        key = (order_id, vendor_item_id, sale_type, row.get("recognition_date"))
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        date = str(row.get("recognition_date") or "")
        if _DATE.match(date):
            dates.append(date[:10])
        entry = items.setdefault(vendor_item_id, {
            "vendor_item_id": vendor_item_id, "name": None, "sale_orders": set(), "sale_units": 0, "sale_amount": Decimal(0), "service_fee": Decimal(0),
            "settlement_amount": Decimal(0), "refund_orders": set(), "refund_units": 0, "refund_amount": Decimal(0),
        })
        if not entry["name"] and row.get("name"):
            entry["name"] = str(row["name"])[:60]
        if sale_type == "SALE":
            entry["sale_orders"].add(order_id)
            all_sale_orders.add(order_id)
            entry["sale_units"] += quantity
            entry["sale_amount"] += amount
            entry["service_fee"] += fee
            entry["settlement_amount"] += settlement
        else:
            entry["refund_orders"].add(order_id)
            entry["refund_units"] += quantity
            entry["refund_amount"] += amount

    out_items = []
    for entry in items.values():
        orders = len(entry["sale_orders"])
        level = sample_level(orders)
        return_rate = _ratio(Decimal(entry["refund_units"]), Decimal(entry["sale_units"]))
        out_items.append({
            "vendor_item_id": entry["vendor_item_id"], "name": entry["name"], "orders": orders, "sample": level,
            "sale_units": entry["sale_units"], "sale_amount": str(entry["sale_amount"].quantize(_WON, rounding=ROUND_HALF_UP)),
            "avg_unit_price": None if entry["sale_units"] <= 0 else str((entry["sale_amount"] / entry["sale_units"]).quantize(_WON, rounding=ROUND_HALF_UP)),
            "fee_ratio": None if _ratio(entry["service_fee"], entry["sale_amount"]) is None else str(_ratio(entry["service_fee"], entry["sale_amount"])),
            "settlement_amount": str(entry["settlement_amount"].quantize(_WON, rounding=ROUND_HALF_UP)),
            "refund_units": entry["refund_units"], "refund_orders": len(entry["refund_orders"]),
            "refund_amount": str(entry["refund_amount"].quantize(_WON, rounding=ROUND_HALF_UP)),
            "return_rate": None if return_rate is None else str(return_rate),
            "high_return": bool(return_rate is not None and level != "INSUFFICIENT" and return_rate >= HIGH_RETURN_RATE),
            "refund_without_sale": entry["sale_units"] == 0 and entry["refund_units"] > 0,
        })
    out_items.sort(key=lambda item: (-item["sale_units"], -item["refund_units"], item["vendor_item_id"]))

    total_units = sum(item["sale_units"] for item in out_items)
    total_refund_units = sum(item["refund_units"] for item in out_items)
    total_amount = sum((Decimal(item["sale_amount"]) for item in out_items), Decimal(0))
    total_fee = sum((entry["service_fee"] for entry in items.values()), Decimal(0))
    total_return_rate = _ratio(Decimal(total_refund_units), Decimal(total_units))
    total_fee_ratio = _ratio(total_fee, sum((entry["sale_amount"] for entry in items.values()), Decimal(0)))
    return {
        "period": {"from": min(dates) if dates else None, "to": max(dates) if dates else None},
        "totals": {
            "orders": len(all_sale_orders), "sample": sample_level(len(all_sale_orders)), "items": len(out_items), "sale_units": total_units,
            "sale_amount": str(total_amount.quantize(_WON, rounding=ROUND_HALF_UP)), "refund_units": total_refund_units,
            "return_rate": None if total_return_rate is None else str(total_return_rate),
            "fee_ratio": None if total_fee_ratio is None else str(total_fee_ratio),
        },
        "items": out_items[:MAX_ITEMS_SHOWN], "omitted": max(0, len(out_items) - MAX_ITEMS_SHOWN),
        "duplicates": duplicates, "skipped": skipped,
        "thresholds": {"reference_orders": REFERENCE_ORDERS, "judgment_orders": JUDGMENT_ORDERS, "high_return_rate": str(HIGH_RETURN_RATE)},
        "notes": list(NOTES),
    }
