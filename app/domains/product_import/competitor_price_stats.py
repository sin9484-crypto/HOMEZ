"""
=========================================================
Homez OS

File : app/domains/product_import/competitor_price_stats.py

2026-10-18 — 마케팅 AI M2: 경쟁 가격 **결정적 통계**. 순수 함수, AI·네트워크·DB 없음. 금액은 모두 Decimal이다.

들어오는 것은 사용자가 확인했거나 직접 입력한 항목뿐이다:
  - 출처가 `AI_PAGE`(AI가 페이지에서 읽음)인 항목은 `confirmed`(사용자가 페이지에서 직접 확인했다는 표시)가 있어야 계산에 들어간다. 없으면 제외하고 이유를 남긴다.
  - 출처가 `MANUAL`(사용자가 직접 입력)인 항목은 그대로 쓴다.
  - 같은 주소는 한 번만 센다.

계산:
  - 비교 기준(`basis`): 모든 계산 대상 항목에 배송비가 있으면 `total`(가격+배송비), 하나라도 모르면 `display`(표시가) — 서로 다른 기준을 섞지 않는다.
  - 최저·중앙값·최고(항목이 2개 이상일 때만 중앙값/최고를 낸다), 개당 가격(수량이 있는 항목), 용량 단위 가격(수량과 용량이 모두 있는 항목, ml·l→ml, g·kg→g로 환산하고 단위가 다른 항목은 섞지 않는다).
  - 항목이 3개 이상일 때 중앙값의 2배를 넘거나 절반에 못 미치는 값은 "다른 구성일 수 있다"로 표시한다(제외하지 않는다).
이 값은 **표시가일 뿐 실제 판매 실적이 아니다** — 결과에 고정 문구로 남긴다.
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP
from decimal import Decimal
from typing import Any

WON = Decimal("1")
CENT = Decimal("0.01")
UNUSUAL_RATIO = Decimal("2")
MIN_UNUSUAL_SAMPLE = 3
BASE_UNITS = {"ml": ("ml", Decimal("1")), "l": ("ml", Decimal("1000")), "g": ("g", Decimal("1")), "kg": ("g", Decimal("1000"))}

NOTES = (
    "표시된 가격일 뿐 실제 판매 실적이 아니에요. 할인·쿠폰·배송비·옵션 차이로 실제 결제 금액은 다를 수 있어요.",
    "페이지를 읽은 시각의 가격이에요. 가격은 수시로 바뀌어요.",
)


@dataclass(frozen=True)
class CompetitorEntry:
    label: str
    price: int
    url: str | None = None
    shipping_fee: int | None = None
    pack_count: int | None = None
    unit_volume: Decimal | None = None
    unit: str | None = None
    source: str = "MANUAL"            # MANUAL | AI_PAGE
    confirmed: bool = False
    checked_at: str | None = None


def _round(value: Decimal, quantum: Decimal = WON) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def url_key(url: str | None) -> str | None:
    """같은 주소 판별용: 앞뒤 공백·#조각·끝의 /를 없애고 소문자로."""

    if not url:
        return None
    return url.strip().split("#", 1)[0].rstrip("/").lower() or None


def median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def range_stats(values: list[Decimal], quantum: Decimal = WON) -> dict[str, Any]:
    """항목이 2개 이상일 때만 중앙값·최고를 낸다(1개로 시장 구간을 만들지 않는다)."""

    if not values:
        return {"n": 0, "min": None, "median": None, "max": None}
    if len(values) < 2:
        return {"n": 1, "min": str(_round(values[0], quantum)), "median": None, "max": None}
    return {"n": len(values), "min": str(_round(min(values), quantum)), "median": str(_round(median(values), quantum)), "max": str(_round(max(values), quantum))}


def compare(entries: list[CompetitorEntry]) -> dict[str, Any]:
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        row: dict[str, Any] = {
            "label": entry.label, "url": entry.url, "source": entry.source, "confirmed": entry.confirmed, "checked_at": entry.checked_at,
            "price": str(Decimal(entry.price)), "shipping_fee": None if entry.shipping_fee is None else str(Decimal(entry.shipping_fee)),
            "pack_count": entry.pack_count, "included": False, "reason": None,
        }
        key = url_key(entry.url)
        if key and key in seen:
            row["reason"] = "같은 주소가 이미 있어 한 번만 센다"
        elif entry.source == "AI_PAGE" and not entry.confirmed:
            row["reason"] = "AI가 읽은 값을 페이지에서 직접 확인했다고 표시하지 않아 계산에서 뺐다"
        else:
            row["included"] = True
            if key:
                seen.add(key)
        rows.append(row)

    included = [(entry, row) for entry, row in zip(entries, rows) if row["included"]]
    if not included:
        return {"basis": None, "n": 0, "market": range_stats([]), "per_piece": range_stats([]), "per_volume": [], "entries": rows,
                "warnings": ["계산에 쓸 수 있는 항목이 없다 — AI가 읽은 값은 페이지에서 직접 확인했다고 표시해야 하고, 직접 입력한 값은 바로 쓸 수 있다"], "notes": list(NOTES)}

    basis = "total" if all(e.shipping_fee is not None for e, _ in included) else "display"
    if basis == "display" and len(included) > 1:
        warnings.append("배송비를 모르는 항목이 있어 표시가 기준으로 비교했다 — 배송비까지 비교하려면 모든 항목의 배송비를 입력해야 한다")
    values: list[tuple[Decimal, dict, CompetitorEntry]] = []
    for entry, row in included:
        value = Decimal(entry.price) + (Decimal(entry.shipping_fee) if basis == "total" else Decimal(0))
        row["compared_price"] = str(_round(value))
        values.append((value, row, entry))

    market = range_stats([v for v, _, _ in values])
    if market["n"] < 2:
        warnings.append("계산에 쓴 항목이 1개라 시장 구간(중앙값·최고)을 만들지 않았다 — 경쟁 가격을 2개 이상 확인해야 한다")

    if len(values) >= MIN_UNUSUAL_SAMPLE:
        mid = median([v for v, _, _ in values])
        for value, row, _ in values:
            row["unusual"] = bool(value > mid * UNUSUAL_RATIO or value * UNUSUAL_RATIO < mid)
        if any(row.get("unusual") for _, row, _ in values):
            warnings.append("중앙값과 크게 다른 항목이 있다 — 구성(수량·용량)이 다른 상품일 수 있으니 확인하세요")
    else:
        for _, row, _ in values:
            row["unusual"] = False

    per_piece_values: list[Decimal] = []
    by_class: dict[str, list[Decimal]] = {}
    class_scale: dict[str, Decimal] = {}
    for value, row, entry in values:
        if entry.pack_count:
            piece = value / entry.pack_count
            row["per_piece"] = str(_round(piece))
            per_piece_values.append(piece)
        if entry.pack_count and entry.unit_volume and entry.unit:
            unit = entry.unit.strip().lower()
            cls, factor = BASE_UNITS.get(unit, (unit, Decimal("1")))
            amount = Decimal(entry.pack_count) * entry.unit_volume * factor
            if amount > 0:
                scale = Decimal("100") if cls in ("ml", "g") else Decimal("1")
                per = value / amount * scale
                row["per_volume"] = {"value": str(_round(per, CENT)), "unit_class": cls, "per": str(scale)}
                by_class.setdefault(cls, []).append(per)
                class_scale[cls] = scale

    ranked = sorted(values, key=lambda item: item[0])
    for position, (_, row, _) in enumerate(ranked, start=1):
        row["rank"] = position
    per_volume = [{"unit_class": cls, "per": str(class_scale[cls]), **range_stats(vals, CENT)} for cls, vals in sorted(by_class.items())]
    if len(by_class) > 1:
        warnings.append("용량 단위가 서로 달라 단위별로 나눠 계산했다")
    return {"basis": basis, "n": len(values), "market": market, "per_piece": range_stats(per_piece_values), "per_volume": per_volume,
            "entries": rows, "warnings": warnings, "notes": list(NOTES)}
