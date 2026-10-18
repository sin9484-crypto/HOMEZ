"""
=========================================================
Homez OS

File : app/domains/pricing/price_strategy.py

2026-10-18 — 마케팅 AI M3: **가격 전략 제안**. AI 없음, 외부 호출 없음, 저장 없음. 금액은 모두 Decimal이고 응답에는 문자열로 낸다.

세 가지 가격선을 함께 보여 준다:
  - 마진 하한가: 기준 마진율(= max(회사 설정, 18%))을 지키는 가장 낮은 판매가(Phase E 계산이 낸 값, 10원 올림 + 정확한 미달 보정).
  - 시장 구간: M2 경쟁 가격 비교의 최저·중앙값·최고(표시가일 뿐 실제 판매 실적이 아니다).
  - 권장 구간: 하한가 이상이면서 시장에서 통할 수 있는 구간.

규칙(코드가 강제한다):
  - **어떤 제안도 마진 하한가 아래로 내려가지 않는다.** 경쟁 가격이 더 낮아도 하한가를 넘는 제안만 낸다.
  - 모르는 값(공급가·부가세·배송비·수수료율)이 하나라도 있거나 확인이 필요한 위험이 남아 있으면 가격을 만들지 않고 `HOLD`, 금지 품목·달성 불가 수수료 구조는 `NOT_RECOMMENDED`.
  - 시장 구간이 없거나 항목이 1개뿐이면 하한가만 기준으로 하고 "시장 대비 판단 불가"를 밝힌다.
  - 하한가가 시장 최고가보다 높으면 "가격 경쟁력이 없다"를 밝힌다(제안은 하한가이며 사용자가 다른 공급처·상품을 판단해야 한다).
  - 가격은 바꾸지 않는다. 가격 변경은 기존 가격 변경 요청 → 승인 경로로만 한다.

`plan`은 순수 함수(마진 계산을 함수로 받는다), `strategy_report`는 Phase E 계산을 불러 `plan`에 연결한다.
=========================================================
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from dataclasses import replace
from decimal import Decimal
from typing import Any
from typing import Callable

TEN = Decimal(10)
CENT = Decimal("0.01")
SENSITIVITY_STEP = Decimal("0.02")
MAX_MARKET_N = 10

NOTES = (
    "시장 가격은 경쟁사가 표시한 가격일 뿐 실제 판매 실적이 아니에요. 할인·쿠폰·배송비·옵션 차이로 실제 결제 금액은 달라요.",
    "이 화면은 가격을 바꾸지 않아요. 가격 변경은 가격 변경 요청을 만들고 승인한 뒤에만 반영돼요.",
    "마진 하한가 아래의 가격은 제안하지 않아요. 경쟁 가격이 더 낮아도 마찬가지예요.",
)

MarginAt = Callable[[Decimal], dict[str, Any]]  # 판매가 → {"margin_amount": Decimal, "margin_rate": Decimal, "meets_min": bool}


def up10(value: Decimal) -> Decimal:
    return Decimal(math.ceil(value / TEN) * 10)


def down10(value: Decimal) -> Decimal:
    return Decimal(math.floor(value / TEN) * 10)


@dataclass(frozen=True)
class Market:
    """M2 비교 결과에서 가져온 시장 구간. 항목이 2개 이상일 때만 중앙값·최고가 있다."""

    n: int
    basis: str                      # "total"(가격+배송비) | "display"(표시가)
    min: Decimal
    median: Decimal | None = None
    max: Decimal | None = None

    def usable_range(self) -> bool:
        return self.n >= 2 and self.median is not None and self.max is not None


def _row(key: str, label: str, price: Decimal, margin_at: MarginAt) -> dict[str, Any]:
    m = margin_at(price)
    return {
        "key": key, "label": label, "labels": [label], "price": str(price), "margin_amount": str(m["margin_amount"].quantize(CENT)),
        "margin_rate": str(m["margin_rate"]), "meets_min": bool(m["meets_min"]),
    }


def plan(floor: Decimal, market: Market | None, margin_at: MarginAt, current_price: Decimal | None = None) -> dict[str, Any]:
    """마진 하한가·시장 구간·(선택) 입력 판매가로 권장 구간과 가격선을 만든다."""

    floor = up10(floor) if floor % TEN else floor
    warnings: list[str] = []
    low: Decimal
    high: Decimal | None = None
    suggested: Decimal
    if market is None:
        position, low, suggested = "NO_MARKET", floor, floor
        warnings.append("경쟁 가격이 없어 시장과 비교하지 못했다 — 마진 하한가만 기준으로 했다")
    elif not market.usable_range():
        position = "ONE_REFERENCE"
        low, high, suggested = floor, max(floor, down10(market.min)), floor
        warnings.append("경쟁 가격이 1곳뿐이라 시장 구간을 만들지 못했다 — 참고만 하세요")
    elif floor <= market.median:
        position = "COMPETITIVE"
        low = max(floor, up10(market.min))
        high = max(low, down10(market.median))
        suggested = low
    elif floor <= market.max:
        position = "ABOVE_MEDIAN"
        low, high, suggested = floor, max(floor, down10(market.max)), floor
        warnings.append("마진 하한가가 시장 중앙값보다 높다 — 중앙값보다 비싸게 팔아야 기준 마진이 남는다")
    else:
        position = "ABOVE_MARKET"
        low, high, suggested = floor, None, floor
        warnings.append("마진 하한가가 경쟁 가격 최고가보다 높다 — 가격 경쟁력이 없다. 다른 공급처나 상품을 판단해야 한다")
    if market is not None and market.basis == "display":
        warnings.append("경쟁 가격이 배송비를 뺀 표시가 기준이라 우리 배송비 부담과 정확히 비교되지 않는다")

    # 어떤 제안도 하한가 아래로 내려가지 않는다 — 위 계산이 어떻게 바뀌어도 마지막에 한 번 더 막는다.
    low, suggested = max(low, floor), max(suggested, floor)
    if high is not None:
        high = max(high, low)

    by_price: dict[Decimal, dict[str, Any]] = {floor: _row("floor", "마진 하한가", floor, margin_at)}

    def add(key: str, label: str, price: Decimal | None) -> None:
        if price is None:
            return
        price = max(price, floor)
        if price in by_price:
            by_price[price]["labels"].append(label)  # 같은 가격이면 한 줄에 이름만 더한다
        else:
            by_price[price] = _row(key, label, price, margin_at)

    if market is not None:
        add("market_min", "시장 최저가", up10(market.min))
        if market.usable_range():
            add("market_median", "시장 중앙값", up10(market.median))
            add("market_max", "시장 최고가", up10(market.max))
    add("suggested", "제안가", suggested)
    if high is not None:
        add("recommended_high", "권장 구간 상단", high)
    points = [by_price[price] for price in sorted(by_price)]

    result: dict[str, Any] = {
        "position": position, "floor_price": str(floor),
        "recommended": {"low": str(low), "high": None if high is None else str(high), "suggested": str(suggested)},
        "points": points, "warnings": warnings,
        "market": None if market is None else {
            "n": market.n, "basis": market.basis, "min": str(market.min),
            "median": None if market.median is None else str(market.median), "max": None if market.max is None else str(market.max),
        },
    }
    if current_price is not None:
        result["entered"] = _entered(current_price, floor, market, margin_at)
        if current_price < floor:
            warnings.append("입력한 판매가가 마진 하한가보다 낮다 — 이 가격으로는 기준 마진을 지키지 못한다")
    return result


def _entered(price: Decimal, floor: Decimal, market: Market | None, margin_at: MarginAt) -> dict[str, Any]:
    row = _row("entered", "입력한 판매가", price, margin_at)
    if price < floor:
        row["vs_floor"] = "BELOW_FLOOR"
    else:
        row["vs_floor"] = "OK"
    if market is None:
        row["vs_market"] = "NO_MARKET"
    elif price < market.min:
        row["vs_market"] = "BELOW_MARKET_MIN"
    elif market.usable_range() and price > market.max:
        row["vs_market"] = "ABOVE_MARKET_MAX"
    elif market.usable_range() and price > market.median:
        row["vs_market"] = "ABOVE_MEDIAN"
    else:
        row["vs_market"] = "WITHIN"
    return row


def sensitivity(price: Decimal, margin_with: Callable[[Decimal, Decimal, Decimal], dict[str, Any]]) -> list[dict[str, Any]]:
    """제안가에서 수수료율·반품 예비율이 ±2%p 움직일 때의 마진율. margin_with(price, fee_delta, reserve_delta)."""

    rows = []
    for label, fee_delta, reserve_delta in (
        ("수수료율 +2%p", SENSITIVITY_STEP, Decimal(0)), ("수수료율 -2%p", -SENSITIVITY_STEP, Decimal(0)),
        ("반품 예비율 +2%p", Decimal(0), SENSITIVITY_STEP), ("반품 예비율 -2%p", Decimal(0), -SENSITIVITY_STEP),
    ):
        m = margin_with(price, fee_delta, reserve_delta)
        rows.append({"label": label, "margin_rate": str(m["margin_rate"]), "meets_min": bool(m["meets_min"])})
    return rows


# ---------------------------------------------------------------------------
# Phase E 계산과 연결
# ---------------------------------------------------------------------------

def strategy_report(db, company_id: int, user_id: int, inp, market: Market | None) -> dict[str, Any]:
    """Phase E 계산(`UrlAnalysisService.calculate`)으로 원가·수수료·기준 마진율·하한가를 구하고 `plan`에 연결한다.

    입력 판매가(inp.sale_price)는 품목 판정에서 빼고(그 가격이 낮다는 이유로 품목이 비추천이 되면 안 된다) 따로 평가한다."""

    from app.domains.marketplace_listing.listing_wizard_schema import EconomicsInputItem
    from app.domains.marketplace_listing.margin_calculator import calculate_economics
    from app.domains.marketplace_listing.margin_calculator import min_margin_shortfall
    from app.domains.product_import.url_analysis_service import UrlAnalysisService

    entered = inp.sale_price
    report = UrlAnalysisService(db, None, None).calculate(company_id, user_id, replace(inp, sale_price=None))
    base: dict[str, Any] = {
        "status": "OK", "verdict": report["verdict"], "reasons": list(report["reasons"]), "missing": list(report["missing"]),
        "min_margin_rate": report["min_margin_rate"], "fee": report["fee"], "channel": report["channel"], "notes": list(NOTES),
    }
    if report["verdict"] == "HOLD":
        return {**base, "status": "HOLD"}
    if report["verdict"] == "NOT_RECOMMENDED":
        return {**base, "status": "NOT_RECOMMENDED"}

    min_rate = Decimal(report["min_margin_rate"])
    cost = Decimal(report["cost_of_goods"])
    fee_rate = Decimal(report["fee"]["rate"])
    floor_row = next((s for s in report["scenarios"] if s.get("sale_price") and Decimal(s["target_margin_rate"]) == min_rate), None)
    if floor_row is None:
        return {**base, "status": "NOT_RECOMMENDED", "reasons": base["reasons"] + ["기준 마진율을 지키는 판매가를 만들 수 없다"]}

    def clamp(value: Decimal) -> Decimal:
        return min(max(value, Decimal(0)), Decimal(1))

    def margin_with(price: Decimal, fee_delta: Decimal = Decimal(0), reserve_delta: Decimal = Decimal(0)) -> dict[str, Any]:
        item = EconomicsInputItem(
            marketplace_account_id=0, cost_of_goods=cost, sale_price=price, channel_fee_rate=clamp(fee_rate + fee_delta),
            payment_fee_rate=inp.payment_fee_rate, shipping_cost=inp.shipping_cost, packaging_cost=inp.packaging_cost,
            return_reserve_rate=clamp(inp.return_reserve_rate + reserve_delta), tax_basis_rate=Decimal(0),
        )
        result = calculate_economics(item)
        return {"margin_amount": result.margin_amount, "margin_rate": result.margin_rate, "meets_min": min_margin_shortfall(item, min_rate) <= 0}

    result = plan(Decimal(floor_row["sale_price"]), market, lambda price: margin_with(price), entered)
    suggested = Decimal(result["recommended"]["suggested"])
    return {**base, **result, "sensitivity": sensitivity(suggested, margin_with), "assumptions": report["assumptions"]}
