"""
=========================================================
Homez OS

File : app/domains/supplier/offer_rules.py

2026-10-23 — 상품 서칭 AI S3(반영 D, "공급처 비의존"): **어느 공급처에서 온 상품이든 같은 규칙으로 평가하는** 순수 계산. 공급처 이름(온채널·오너클랜·도매매·직접 입력)을 규칙이 모른다 —
입력은 공급가·배송비·반품 배송비·최소 주문 수량·위탁 가능 여부 같은 **값**뿐이다. DB·네트워크·AI·파일 없음.

왜 만들었나(2026-10-21 점검·공급처 조사):
  - 개당 7,000~12,000원대 의류는 판매자 부담 배송비 3,000원이 마진을 좌우한다. 매입 15,000원 상품을 19,900원에 팔면 −3.3%, 매입 7,000원 상품은 14,451원 이상이어야 마진 18%다.
  - 온채널에 기대지 않고 후보 공급처를 같은 잣대로 비교해야 한다(사용자 지시 "온채널에 의지하지 마라").

계산(HOMEZ가 백산수 승인 경제성에 쓴 값과 같은 식 — 시험이 그 기록값 2,197.60원·18.24%를 재현한다):
  마진 = (판매가 × (1 − 수수료율 − 반품 예비율) − 공급가 − 배송비) ÷ 판매가
  기준 마진율(18%)을 지키는 최저 판매가 = (공급가 + 배송비) ÷ (1 − 수수료율 − 반품 예비율 − 기준 마진율)
수수료율·반품 예비율·기준 마진율은 호출하는 쪽이 넘긴다 — 기본값은 HOMEZ 기록값(9.8%·3%·18%)이며 **의류의 실제 쿠팡 수수료율은 확인하지 못한 가정**이다(🔴).

점검(이유 코드와 수준 BLOCK/HOLD/WARN/INFO/OK):
  - COST_UNKNOWN(HOLD)         공급가·배송비를 모른다 — "모르는 비용을 괜찮다고 보지 않는다"(Phase E·S2와 같은 원칙). 가격이 회원 전용이어도 같다(PRICE_HIDDEN)
  - DROPSHIP_NO(BLOCK)         위탁(무재고 발송)이 안 된다 — HOMEZ 모델과 맞지 않는다 / DROPSHIP_UNKNOWN(HOLD) 모른다
  - MIN_ORDER_ABOVE_ONE(BLOCK) 최소 주문 수량이 1보다 크다 — 주문 1건당 1개를 보낼 수 없다
  - HIGH_SHIPPING_SHARE(WARN)  배송비가 (공급가+배송비)의 30% 이상 — 저단가에서 배송비가 마진을 먹는다
  - RETURN_BURDEN_HIGH(WARN)   반품 왕복 배송비가 공급가의 50% 이상 — 반품 한 건이 곧 손실이다 / RETURN_COST_UNKNOWN(INFO)
  - TARGET_PRICE_BELOW_FLOOR(BLOCK) 기대 판매가에서 마진이 기준에 못 미친다(판매가를 줄 때만)
  - NO_API(INFO) 주문 연동 API가 없다(사람이 발주) / API_UNKNOWN(INFO) / NO_DOMESTIC_STOCK(INFO) 국내 재고가 아니다(배송 기간·반품 제한)
결정: BLOCK이 있으면 EXCLUDE, 없고 HOLD가 있으면 HOLD, 모두 넘으면 READY. 권고일 뿐 — 공급처 선택·후보 승인은 사용자가 한다.

반올림: 원 단위 half-up(`Decimal` 정밀). 같은 입력이면 같은 출력·같은 순서(결정적).
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP
from decimal import Decimal
from typing import Any

BLOCK, HOLD, WARN, INFO, OK = "BLOCK", "HOLD", "WARN", "INFO", "OK"
READY, EXCLUDE = "READY", "EXCLUDE"
DECISION_RANK = {READY: 0, HOLD: 1, EXCLUDE: 2}

HIGH_SHIPPING_SHARE = Decimal("0.30")
RETURN_BURDEN_HIGH = Decimal("0.50")
MAX_OFFERS = 50

ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class MarginAssumptions:
    """HOMEZ 기록값(백산수 승인 경제성: 수수료 9.8%·반품 예비 3%·기준 마진 18%). 의류의 실제 수수료율은 🔴 미확인 — 호출하는 쪽이 실제 값을 넘긴다."""

    fee_rate: Decimal = Decimal("0.098")
    return_reserve_rate: Decimal = Decimal("0.03")
    min_margin: Decimal = Decimal("0.18")


@dataclass(frozen=True)
class SupplierOffer:
    """공급처가 어디든 같은 모양. 모르는 값은 None이다(0으로 채우지 않는다)."""

    supplier_code: str
    external_id: str
    title: str
    unit_cost: Decimal | None = None
    shipping_cost: Decimal | None = None
    return_shipping_cost: Decimal | None = None
    min_order_qty: int | None = None
    price_visible: bool = True
    dropship_supported: bool | None = None
    api_available: bool | None = None
    domestic_stock: bool | None = None


def _won(value: Decimal) -> int:
    return int(value.quantize(ONE, rounding=ROUND_HALF_UP))


def _positive(value: Any) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > ZERO


def _non_negative(value: Any) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= ZERO


def _valid_assumptions(a: MarginAssumptions) -> bool:
    rates = (a.fee_rate, a.return_reserve_rate, a.min_margin)
    return all(isinstance(r, Decimal) and r.is_finite() and ZERO <= r < ONE for r in rates) and (a.fee_rate + a.return_reserve_rate + a.min_margin) < ONE


def floor_price(offer: SupplierOffer, a: MarginAssumptions) -> Decimal | None:
    """기준 마진율을 지키는 최저 판매가(소수 그대로). 비용을 모르거나 분모가 0 이하면 None."""

    if not (_positive(offer.unit_cost) and _non_negative(offer.shipping_cost) and _valid_assumptions(a)):
        return None
    return (offer.unit_cost + offer.shipping_cost) / (ONE - a.fee_rate - a.return_reserve_rate - a.min_margin)


def margin_at(offer: SupplierOffer, price: Decimal, a: MarginAssumptions) -> Decimal | None:
    """판매가 `price`에서의 마진율(판매가 대비). 비용을 모르거나 판매가가 0 이하면 None."""

    if not (_positive(offer.unit_cost) and _non_negative(offer.shipping_cost) and _positive(price) and _valid_assumptions(a)):
        return None
    return (price * (ONE - a.fee_rate - a.return_reserve_rate) - offer.unit_cost - offer.shipping_cost) / price


def _reason(code: str, level: str, **params: Any) -> dict[str, Any]:
    return {"code": code, "level": level, "params": params}


def evaluate_offer(offer: SupplierOffer, target_price: Decimal | None = None, assumptions: MarginAssumptions | None = None) -> dict[str, Any]:
    """공급 조건 하나를 평가한다. 반환: decision, reasons, floor_price(원), cost_multiple, shipping_share, return_burden, margin_at_target."""

    a = assumptions or MarginAssumptions()
    reasons: list[dict[str, Any]] = []
    if not _valid_assumptions(a):
        reasons.append(_reason("ASSUMPTIONS_INVALID", HOLD))

    # 비용을 모르면 후보 가능으로 올리지 않는다.
    if not offer.price_visible:
        reasons.append(_reason("PRICE_HIDDEN", HOLD))
    cost_known = _positive(offer.unit_cost) and _non_negative(offer.shipping_cost)
    if not cost_known:
        reasons.append(_reason("COST_UNKNOWN", HOLD))

    if offer.dropship_supported is False:
        reasons.append(_reason("DROPSHIP_NO", BLOCK))
    elif offer.dropship_supported is None:
        reasons.append(_reason("DROPSHIP_UNKNOWN", HOLD))
    if offer.min_order_qty is not None and offer.min_order_qty > 1:
        reasons.append(_reason("MIN_ORDER_ABOVE_ONE", BLOCK, qty=offer.min_order_qty))

    floor = floor_price(offer, a)
    shipping_share = None
    return_burden = None
    margin_target = None
    if cost_known:
        total = offer.unit_cost + offer.shipping_cost
        shipping_share = offer.shipping_cost / total
        if shipping_share >= HIGH_SHIPPING_SHARE:
            reasons.append(_reason("HIGH_SHIPPING_SHARE", WARN, percent=_won(shipping_share * 100)))
        if _non_negative(offer.return_shipping_cost):
            return_burden = offer.return_shipping_cost / offer.unit_cost
            if return_burden >= RETURN_BURDEN_HIGH:
                reasons.append(_reason("RETURN_BURDEN_HIGH", WARN, percent=_won(return_burden * 100)))
        else:
            reasons.append(_reason("RETURN_COST_UNKNOWN", INFO))
        if target_price is not None:
            margin_target = margin_at(offer, target_price, a)
            if margin_target is not None and margin_target < a.min_margin:
                reasons.append(_reason("TARGET_PRICE_BELOW_FLOOR", BLOCK, floor=_won(floor) if floor is not None else None))
    else:
        if not _non_negative(offer.return_shipping_cost):
            reasons.append(_reason("RETURN_COST_UNKNOWN", INFO))

    if offer.api_available is False:
        reasons.append(_reason("NO_API", INFO))
    elif offer.api_available is None:
        reasons.append(_reason("API_UNKNOWN", INFO))
    if offer.domestic_stock is False:
        reasons.append(_reason("NO_DOMESTIC_STOCK", INFO))

    levels = {r["level"] for r in reasons}
    decision = EXCLUDE if BLOCK in levels else (HOLD if HOLD in levels else READY)
    return {
        "supplier_code": offer.supplier_code, "external_id": offer.external_id, "title": offer.title, "decision": decision, "reasons": reasons,
        "floor_price": None if floor is None else _won(floor),
        "cost_multiple": None if floor is None else float((floor / offer.unit_cost).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "shipping_share_percent": None if shipping_share is None else _won(shipping_share * 100),
        "return_burden_percent": None if return_burden is None else _won(return_burden * 100),
        "margin_at_target_percent": None if margin_target is None else float((margin_target * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)),
    }


def rank_offers(offers: list[SupplierOffer], target_price: Decimal | None = None, assumptions: MarginAssumptions | None = None) -> list[dict[str, Any]]:
    """여러 공급 조건을 같은 규칙으로 평가해 순서를 매긴다. 후보 가능 → 보류 → 제외, 같은 묶음 안에서는 목표 판매가의 마진이 큰 것(없으면 최저 판매가가 낮은 것) 먼저,
    그래도 같으면 공급처·번호 순(결정적). 한 번에 `MAX_OFFERS`건까지."""

    if len(offers) > MAX_OFFERS:
        raise ValueError(f"한 번에 {MAX_OFFERS}건까지만 평가할 수 있습니다.")
    results = [evaluate_offer(o, target_price, assumptions) for o in offers]

    def key(r: dict[str, Any]):
        margin = r["margin_at_target_percent"]
        floor = r["floor_price"]
        return (
            DECISION_RANK[r["decision"]],
            0 if margin is not None else 1, -(margin if margin is not None else 0),
            0 if floor is not None else 1, floor if floor is not None else 0,
            r["supplier_code"], r["external_id"],
        )

    ordered = sorted(results, key=key)
    for index, row in enumerate(ordered, start=1):
        row["rank"] = index
    return ordered


__all__ = [
    "MarginAssumptions", "SupplierOffer", "floor_price", "margin_at", "evaluate_offer", "rank_offers", "BLOCK", "HOLD", "WARN", "INFO", "OK",
    "READY", "EXCLUDE", "MAX_OFFERS", "HIGH_SHIPPING_SHARE", "RETURN_BURDEN_HIGH",
]
